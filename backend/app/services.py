import subprocess
import os
import re
import shutil
import time
import threading
import queue
import datetime
import json
from pathlib import Path
from typing import List, Dict, Optional, Generator
from .schemas import YmlFile, RepoInfo
from .logger import log_service


# docker/plain 输出里常见的：
#   (v2) 7c3c483d20b5 Downloading [====>  ] 44.82MB/210.4MB
#   (v1) 70ba6939098d Downloading 19.43MB
#   (v1) 05938142326a Extracting 867.9kB
#   (尾) eb914bcc923c Download complete | Pull complete
# Layer id 一般是行首或最前面的 12 位 hex。
_SIZE_WITH_DENOM_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB|kB|kb|mb|gb)\s*/\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB|kB|kb|mb|gb)"
)
_SIZE_ABS_RE = re.compile(
    r"(?:Downloading|Extracting|downloading|extracting)\s+(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB|kB|kb|mb|gb)",
    re.IGNORECASE,
)
_LAYER_ID_RE = re.compile(r"(?<!\w)([0-9a-f]{12})(?!\w)")
_UNIT_MULT = {"b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4}
_TOTAL_PROGRESS_STAGES = ("pull", "up")

# v1 没有分母的 layer，用软上限估算 total。
_DEFAULT_SOFT_CAPS = [
    64 * 1024 ** 2,
    256 * 1024 ** 2,
    1024 * 1024 ** 2,
    4 * 1024 ** 3,
    16 * 1024 ** 3,
    64 * 1024 ** 3,
]


def _human_bytes(n):
    if n is None:
        return "?"
    for u, mult in (("TB", 1024 ** 4), ("GB", 1024 ** 3),
                    ("MB", 1024 ** 2), ("KB", 1024), ("B", 1)):
        if n >= mult:
            return f"{n / mult:.2f}{u}"
    return f"{n:.0f}B"


def _choose_layer_soft_cap(cur_bytes: float, last_cap: float) -> float:
    base_cap = max(last_cap, _DEFAULT_SOFT_CAPS[0])
    if cur_bytes < base_cap:
        return base_cap
    for cap in _DEFAULT_SOFT_CAPS:
        if cap >= cur_bytes:
            return cap
    return cur_bytes * 2


def _extract_layer_id(line: str):
    if not line:
        return None
    m = _LAYER_ID_RE.search(line)
    return m.group(1) if m else None


def _parse_progress_line(line: str):
    """从单行原始 plain 文本抽取结构化进度信息，解析失败返回 None。

    返回 dict:
      layer_id: str or None
      phase:    'download' | 'extract' | 'complete' | None
      cur_b:    float or None (当前字节，已 complete 的层取已记录 total 当 cur)
      total_b:  float or None (真实 total 字节，v1/complete 可能为空)
      detail:   str (人类可读，用于前端进度条下方文字)
    """
    if not line:
        return None
    stripped = line.strip()
    layer_id = _extract_layer_id(stripped)

    # --- 1) 完成类: Download complete / Pull complete ---
    if (layer_id and
        ("Download complete" in stripped or "Pull complete" in stripped
         or "Verifying Checksum" in stripped and "Download complete" in stripped)):
        # Verifying Checksum 有时和 complete 同行就按 complete 算
        if "Pull complete" in stripped or "Download complete" in stripped:
            return {"layer_id": layer_id, "phase": "complete",
                    "cur_b": None, "total_b": None,
                    "detail": f"{layer_id or 'layer'} complete"}

    # --- 2) 带分子/分母 (v2 或 v1 某些阶段) ---
    m = _SIZE_WITH_DENOM_RE.search(stripped)
    if m:
        cur, cur_unit, total, total_unit = m.groups()
        try:
            cur_b = float(cur) * _UNIT_MULT[cur_unit.lower()]
            total_b = float(total) * _UNIT_MULT[total_unit.lower()]
        except Exception:
            return None
        if total_b <= 0:
            return None
        phase = ("download" if ("Downloading" in stripped or "downloading" in stripped)
                 else "extract" if ("Extracting" in stripped or "extracting" in stripped)
                 else None)
        detail = f"{phase or 'progress'}: {cur}{cur_unit} / {total}{total_unit}"
        return {"layer_id": layer_id, "phase": phase,
                "cur_b": cur_b, "total_b": total_b, "detail": detail}

    # --- 3) 只有绝对大小 (v1 典型) ---
    m = _SIZE_ABS_RE.search(stripped)
    if m:
        abs_str, unit = m.groups()
        try:
            cur_b = float(abs_str) * _UNIT_MULT[unit.lower()]
        except Exception:
            return None
        phase = m.group(0).split()[0].lower()  # downloading / extracting
        detail = f"{phase}: {abs_str}{unit}"
        return {"layer_id": layer_id, "phase": phase,
                "cur_b": cur_b, "total_b": None, "detail": detail}

    # --- 4) Waiting / Verifying Checksum / Pulling fs layer 这些无进度数值，
    # 只要有 layer_id 就返回，给调用方标记该 layer「见过」但无数字更新 ---
    if layer_id:
        hint = None
        if "Pulling fs layer" in stripped:
            hint = "pulling fs layer"
        elif "Waiting" in stripped:
            hint = "waiting"
        elif "Verifying Checksum" in stripped:
            hint = "verifying checksum"
        if hint:
            return {"layer_id": layer_id, "phase": "meta",
                    "cur_b": None, "total_b": None, "detail": f"{layer_id} {hint}"}

    return None


def _aggregate_percent(layers: dict):
    """根据 layers dict 重算全局百分比：Σcur / Σeff_total * 100。

    layers 结构: {layer_id: {cur, total, soft_cap, complete}}
    eff_total 取：有真实 total 就用 total；complete 用 cur；否则 soft_cap。
    未 complete 的层最多算 99.9%，防止"小层全部完了大层还在下"就显示 100% 的误导。
    返回 (pct_0_to_999, detail_desc)。"""
    sum_cur = 0.0
    sum_total = 0.0
    any_unfinished = False
    largest_active_layer_detail = ""
    largest_active_ratio = -1.0
    for lid, l in layers.items():
        cur = l.get("cur") or 0.0
        total_real = l.get("total")
        soft_cap = l.get("soft_cap") or _DEFAULT_SOFT_CAPS[0]
        complete = bool(l.get("complete"))
        if complete:
            eff_total = total_real or max(cur, soft_cap)
            eff_cur = eff_total
        else:
            any_unfinished = True
            eff_total = total_real if total_real else soft_cap
            eff_cur = min(cur, eff_total) if cur > 0 else 0.0
            # 为了进度条下方 detail 能看到最"活跃"的层（进度最多的那层），挑 ratio 最高未完成的
            if eff_total > 0:
                ratio = eff_cur / eff_total
                if ratio > largest_active_ratio:
                    largest_active_ratio = ratio
                    largest_active_layer_detail = (
                        f"{lid}: {_human_bytes(eff_cur)} / "
                        f"{_human_bytes(eff_total)}"
                        + ("" if total_real else " (估)")
                    )
        sum_cur += eff_cur
        sum_total += eff_total
    if sum_total <= 0:
        return 0.0, ""
    pct = sum_cur / sum_total * 100.0
    if any_unfinished:
        pct = min(pct, 99.9)
    # detail：优先显示最活跃层，否则显示汇总
    if largest_active_layer_detail:
        detail = largest_active_layer_detail
    else:
        detail = f"{_human_bytes(sum_cur)} / {_human_bytes(sum_total)}"
    return pct, detail


def _now_iso():
    """返回东八区（UTC+8）本地时间的 ISO 字符串，秒级，固定不管服务器系统时区。"""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Shanghai")
    except Exception:
        # Py < 3.9 或无 IANA tzdata 时用固定偏移 +8:00
        tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz).isoformat(timespec="seconds")


def _stream_command(cmd: list, env: dict, timeout: int, stage: str, label: str, deployment_logs=None) -> Generator[dict, None, int]:
    """运行子命令并逐行 yield 日志事件，最后返回进程 returncode。

    合并 stdout/stderr，按 \\r 和 \\n 实时分段，让 docker pull 的进度条
    也能流式输出。用后台线程读管道，主循环用短轮询+心跳避免页面假死。

    事件类型:
      {"type":"log", ..., "ts": ISO时间, "message": "..."}
      {"type":"progress", ..., "ts": ISO时间, "stage": str, "percent": 0-100,
       "detail": "Downloading 12MB/124MB", "elapsed_sec": float,
       "eta_sec": float|null}
      {"type":"done", ..., "ts": ISO时间, ...}

    timeout 是「空闲超时」：连续 timeout 秒没有任何输出才 kill 进程，
    不是总时长上限——慢网下拉大镜像只要持续有进度刷新就不会被误杀，
    只有真正卡死（代理断了、镜像源挂了）才会触发。
    """
    # stdin=DEVNULL + Windows CREATE_NO_WINDOW：
    # 防止子进程继承到终端后因等待输入或弹出控制台窗口而挂起
    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "env": env,
    }
    if os.name == "nt":
        try:
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        except AttributeError:
            pass

    proc = subprocess.Popen(cmd, bufsize=0, **popen_kwargs)

    # 使用一个独立哨兵对象，区分"读线程 EOF"和"超时无数据"
    EOF_SENTINEL = object()

    out_q: "queue.Queue[object]" = queue.Queue()

    buffer = ""
    HEARTBEAT_INTERVAL = 60.0
    POLL_INTERVAL = 0.2
    PROGRESS_EMIT_INTERVAL = 0.5
    last_line_time = time.time()
    last_heartbeat_time = time.time()
    last_progress_emit = 0.0
    start_idle_time = time.time()
    started_at = time.time()
    heartbeat_seq = 0

    # layers: {layer_id: {cur, total, soft_cap, complete}}。未识别到 layer 时用 None 做 key
    # 存一条 "__fallback__" 兜底条目，便于无 layer id 的行也能参与累计
    layers: dict = {}

    def _update_layer_from_info(info: dict):
        """用 _parse_progress_line 产出的结构化信息更新 layers，返回 (changed: bool)。"""
        lid = info.get("layer_id") or "__fallback__"
        l = layers.get(lid)
        if l is None:
            l = {"cur": 0.0, "total": None, "soft_cap": _DEFAULT_SOFT_CAPS[0], "complete": False}
            layers[lid] = l
        phase = info.get("phase")
        cur_b = info.get("cur_b")
        total_b = info.get("total_b")
        if phase == "complete":
            l["complete"] = True
            # 完成时如果之前已知 total_b 或 cur，eff_total 就按它们算；这里只改标志。
            return True
        if total_b is not None:
            # 真实 total（v2 或 v1 某些分母版本）
            if l.get("total") is None or total_b > l["total"]:
                l["total"] = total_b
        if cur_b is not None and cur_b > (l.get("cur") or 0.0):
            l["cur"] = cur_b
            # v1 无分母时需要同步扩 soft_cap（每层独立）
            if l.get("total") is None:
                l["soft_cap"] = _choose_layer_soft_cap(cur_b, l.get("soft_cap") or _DEFAULT_SOFT_CAPS[0])
        return cur_b is not None or total_b is not None or phase == "complete" or phase == "meta"

    stage_pct = 0.0
    stage_last_detail = ""

    def recompute_progress():
        """根据 layers 重算全局 pct 和 detail，更新 stage_pct / stage_last_detail。"""
        nonlocal stage_pct, stage_last_detail
        if not layers:
            return
        pct, detail = _aggregate_percent(layers)
        # 百分比只升不降：聚合结果若比当前低（比如新 layer 冒出来分母很大稀释）则保持原值
        if pct > stage_pct:
            stage_pct = pct
        if detail:
            stage_last_detail = detail

    def push_progress(force=False):
        """基于 stage_pct / stage_last_detail 推 SSE progress 事件（限频）。"""
        nonlocal last_progress_emit
        now = time.time()
        if (now - last_progress_emit) < PROGRESS_EMIT_INTERVAL and not force:
            return None
        elapsed = now - started_at
        pct_effective = max(0.1, min(99.99, stage_pct))
        if stage_pct >= 99.9:
            eta = 0.0
        else:
            eta = max(0.0, (elapsed / pct_effective) * (100.0 - pct_effective))
        last_progress_emit = now
        return {
            "type": "progress",
            "stage": stage,
            "percent": round(stage_pct, 2),
            "detail": stage_last_detail,
            "elapsed_sec": round(elapsed, 1),
            "eta_sec": round(eta, 1),
            "ts": _now_iso(),
        }

    def flush_buffer(force: bool = False):
        nonlocal buffer, last_line_time
        decoded_chunks = 0
        parts = re.split(r"[\r\n]", buffer)
        if not force:
            keep, emit = parts[-1], parts[:-1]
        else:
            keep, emit = "", parts
        buffer = keep
        for part in emit:
            line = part.rstrip()
            if not line:
                continue
            decoded_chunks += 1

            if stage in _TOTAL_PROGRESS_STAGES:
                info = _parse_progress_line(line)
                if info:
                    _update_layer_from_info(info)
                    recompute_progress()
                    if stage_pct > 0 or stage_last_detail:
                        pev = push_progress(force=False)
                        if pev is not None:
                            yield pev

            msg = f"{label} {line}"
            if deployment_logs is not None:
                deployment_logs.append(msg)
            yield {"type": "log", "level": "info", "stage": stage, "message": msg, "ts": _now_iso()}
        if decoded_chunks:
            last_line_time = time.time()

    def _reader():
        try:
            stdout = proc.stdout
            while True:
                try:
                    chunk = stdout.read1(4096)  # type: ignore[union-attr]
                except AttributeError:
                    # 某些 Python/Windows 组合未实现 read1，退化成 read
                    chunk = stdout.read(4096)  # type: ignore[union-attr]
                if not chunk:
                    break
                out_q.put(chunk)
        except Exception:
            pass
        finally:
            out_q.put(EOF_SENTINEL)

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    try:
        while True:
            # 短轮询：每次最多等 POLL_INTERVAL，方便穿插心跳和空闲计时
            remaining_idle = (timeout - (time.time() - start_idle_time)) if timeout else None
            if remaining_idle is not None:
                wait_timeout = min(POLL_INTERVAL, max(0.05, remaining_idle))
            else:
                wait_timeout = POLL_INTERVAL
            try:
                item = out_q.get(timeout=wait_timeout)
            except queue.Empty:
                item = None

            now = time.time()

            # --- 情况 1：超时（本轮没拿到任何队列条目）
            if item is None:
                if proc.poll() is None:
                    # 进程仍在运行
                    if timeout and (now - start_idle_time) > timeout:
                        proc.kill()
                        raise subprocess.TimeoutExpired(cmd, timeout)
                    if (now - last_heartbeat_time) >= HEARTBEAT_INTERVAL:
                        heartbeat_seq += 1
                        elapsed = int(now - last_line_time)
                        msg = f"{label} [心跳 #{heartbeat_seq}] 仍在运行中，{elapsed}s 无新输出，请稍候..."
                        if deployment_logs is not None:
                            deployment_logs.append(msg)
                        yield {"type": "log", "level": "info", "stage": stage, "message": msg, "ts": _now_iso()}
                        # 心跳时顺手推进一次 progress（刷新 elapsed/ETA；pct/detail 从已缓存的 stage_pct / stage_last_detail 取）
                        if stage in _TOTAL_PROGRESS_STAGES:
                            pev = push_progress(force=False)
                            if pev is not None:
                                yield pev
                        last_heartbeat_time = now
                    continue
                # 进程已退出但还没收到 EOF_SENTINEL：继续循环收末尾数据
                continue

            # --- 情况 2：读线程说 EOF
            if item is EOF_SENTINEL:
                break

            # --- 情况 3：实际字节数据
            chunk = item  # bytes
            start_idle_time = now
            buffer += chunk.decode("utf-8", errors="replace")
            for ev in flush_buffer(force=False):
                yield ev

        # 结束前：强制把最后一段残留 flush 出来，再推送一条 100% 进度事件
        for ev in flush_buffer(force=True):
            yield ev
        if stage in _TOTAL_PROGRESS_STAGES:
            final_total_elapsed = time.time() - started_at
            final_progress_event = {
                "type": "progress",
                "stage": stage,
                "percent": 100.0,
                "detail": stage_last_detail or "完成",
                "elapsed_sec": round(final_total_elapsed, 1),
                "eta_sec": 0.0,
                "ts": _now_iso(),
            }
            yield final_progress_event
            # 同时 append 一条 summary 到日志，便于事后查询耗时
            summary = f"{label} [{stage}] 阶段完成，耗时 {round(final_total_elapsed, 1)} 秒"
            if deployment_logs is not None:
                deployment_logs.append(summary)
            yield {"type": "log", "level": "info", "stage": stage, "message": summary, "ts": _now_iso()}
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        reader_thread.join(timeout=2)
    return proc.returncode

REPOS_DIR = Path("./repos")
REPOS_DIR.mkdir(exist_ok=True)

DATA_DIR = Path("./data")
DATA_DIR.mkdir(exist_ok=True)
REPOS_JSON_PATH = DATA_DIR / "repos.json"

# 默认仓库配置（若 JSON 文件不存在则写入）
DEFAULT_REPOS_JSON = [
    {
        "name": "飞牛容器仓库",
        "repo_url": "https://github.com/Double-Stack-Workshop/Compose-File",
        "branch": "main",
        "local_path": "fnOS"
    },
    {
        "name": "绿联新系统容器仓库",
        "repo_url": "https://github.com/Double-Stack-Workshop/Compose-File",
        "branch": "main",
        "local_path": "UgreenNew"
    },
    {
        "name": "绿联旧系统容器仓库",
        "repo_url": "https://github.com/Double-Stack-Workshop/Compose-File",
        "branch": "main",
        "local_path": "Ugreen（Abandoned）"
    },
    {
        "name": "极空间容器仓库",
        "repo_url": "https://github.com/Double-Stack-Workshop/Compose-File",
        "branch": "main",
        "local_path": "ZSpace"
    }
]

from .database import (
    get_proxy_config as db_get_proxy_config,
    set_proxy_config as db_set_proxy_config,
    get_images_cache,
    update_images_cache,
    get_setting,
    set_setting
)

# JSON 读写仓库配置
def load_repos_config() -> List[Dict]:
    """从 repos.json 读取仓库配置（仅 name/repo_url/branch/local_path 4 字段）"""
    if not REPOS_JSON_PATH.exists():
        REPOS_JSON_PATH.write_text(
            json.dumps(DEFAULT_REPOS_JSON, ensure_ascii=False, indent=4),
            encoding="utf-8"
        )
        return DEFAULT_REPOS_JSON
    try:
        content = REPOS_JSON_PATH.read_text(encoding="utf-8")
        data = json.loads(content)
        if not isinstance(data, list):
            return DEFAULT_REPOS_JSON
        return data
    except Exception as e:
        print(f"读取 repos.json 失败，使用默认配置: {e}")
        return DEFAULT_REPOS_JSON

def save_repos_config(repos_list: List[Dict]) -> bool:
    """将仓库配置列表写入 repos.json（列表项只保留 4 字段）"""
    try:
        clean_list = []
        for r in repos_list:
            clean_list.append({
                "name": r.get("name", ""),
                "repo_url": r.get("repo_url", ""),
                "branch": r.get("branch", "main"),
                "local_path": r.get("local_path", "")
            })
        REPOS_JSON_PATH.write_text(
            json.dumps(clean_list, ensure_ascii=False, indent=4),
            encoding="utf-8"
        )
        return True
    except Exception as e:
        print(f"写入 repos.json 失败: {e}")
        return False

# 容器推荐配置 JSON
RECOMMEND_JSON_PATH = DATA_DIR / "recommend.json"
DEFAULT_RECOMMEND_CONFIG = {
    "_tutorial_base_url": "https://blog.doublestack.top/archives/",
    "qbittorrent": {
        "title": "qBittorrent",
        "subtitle": "轻量级 BitTorrent 客户端",
        "description": "功能强大的开源 BitTorrent 客户端，支持远程管理、RSS订阅、Web UI 等功能。",
        "tags": ["下载工具", "BT"],
        "tutorial": "docker-rong-qi-qbittorrent-bu-shu-jiao-cheng"
    },
    "transmission": {
        "title": "Transmission",
        "subtitle": "快速轻量级 BT 客户端",
        "description": "开源的 BitTorrent 客户端，以简洁高效著称，支持 Web 界面远程管理。",
        "tags": ["下载工具", "BT"],
        "tutorial": "docker-rong-qi-transmission-bu-shu-jiao-cheng"
    },
    "emby": {
        "title": "Emby",
        "subtitle": "个人媒体服务器",
        "description": "强大的媒体服务器，支持自动刮削元数据、多设备播放、实时转码、直播电视等功能。",
        "tags": ["媒体", "影音"],
        "tutorial": "docker-rong-qi-emby-bu-shu-jiao-cheng"
    },
    "moviepilot": {
        "title": "MoviePilot",
        "subtitle": "智能媒体库管理工具",
        "description": "NAS 媒体库自动化管理工具，支持自动订阅、刮削、整理、通知等功能。",
        "tags": ["媒体", "自动化"],
        "tutorial": "docker-rong-qi-moviepilot-bu-shu-jiao-cheng"
    },
    "navidrome": {
        "title": "Navidrome",
        "subtitle": "现代音乐流媒体服务器",
        "description": "开源的音乐流媒体服务器，支持 Subsonic API，可在线播放和管理个人音乐库。",
        "tags": ["音乐", "流媒体"],
        "tutorial": "docker-rong-qi-navidrome-bu-shu-jiao-cheng"
    },
    "openlist": {
        "title": "OpenList",
        "subtitle": "网盘文件列表程序",
        "description": "支持多种网盘的文件列表程序，可统一管理阿里云盘、百度网盘、天翼云盘等。",
        "tags": ["网盘", "文件管理"],
        "tutorial": "docker-rong-qi-openlist-bu-shu-jiao-cheng"
    }
}

def _resolve_recommend_tutorials(raw: Dict) -> Dict:
    """将配置里的短路径 tutorial 与 _tutorial_base_url 拼接，并移除公用配置字段"""
    if not isinstance(raw, dict):
        return raw
    data = dict(raw)
    base_url = data.pop("_tutorial_base_url", "") or ""
    result = {}
    for key, value in data.items():
        if isinstance(value, dict) and "tutorial" in value and isinstance(value["tutorial"], str):
            tutorial = value["tutorial"]
            if base_url and tutorial and not tutorial.startswith("http"):
                # 处理 base_url 末尾 / 与 tutorial 开头 / 的重复
                if base_url.endswith("/") and tutorial.startswith("/"):
                    tutorial = tutorial.lstrip("/")
                value = {**value, "tutorial": base_url + tutorial}
        result[key] = value
    return result

def load_recommend_config() -> Dict:
    """从 recommend.json 读取容器推荐配置，文件不存在或损坏时用默认配置并写回。
    返回前会根据 _tutorial_base_url 将短路径 tutorial 拼接为完整 URL。"""
    if not RECOMMEND_JSON_PATH.exists():
        try:
            RECOMMEND_JSON_PATH.write_text(
                json.dumps(DEFAULT_RECOMMEND_CONFIG, ensure_ascii=False, indent=4),
                encoding="utf-8"
            )
        except Exception as e:
            print(f"写入 recommend.json 失败: {e}")
        return _resolve_recommend_tutorials(DEFAULT_RECOMMEND_CONFIG)
    try:
        content = RECOMMEND_JSON_PATH.read_text(encoding="utf-8")
        data = json.loads(content)
        if not isinstance(data, dict):
            return _resolve_recommend_tutorials(DEFAULT_RECOMMEND_CONFIG)
        return _resolve_recommend_tutorials(data)
    except Exception as e:
        print(f"读取 recommend.json 失败，使用默认配置: {e}")
        return _resolve_recommend_tutorials(DEFAULT_RECOMMEND_CONFIG)

# 初始化：从 JSON 加载仓库信息（4 字段），yml_files 等动态字段在内存构建
repos_db: List[RepoInfo] = []
_repos_loaded = False

def _load_repos_from_json():
    global repos_db, _repos_loaded
    if _repos_loaded:
        return

    try:
        json_repos = load_repos_config()
        for repo in json_repos:
            name = repo.get("name", "")
            repo_url = repo.get("repo_url", "")
            branch = repo.get("branch", "main")
            local_path = repo.get("local_path", "")
            actual_repo_dir_name = get_repo_name_from_url(repo_url)
            repo_dir = REPOS_DIR / actual_repo_dir_name

            # 动态扫描 yml 文件（如果本地已 clone 完成）
            yml_files = []
            last_sync = "未同步"
            status = "active"
            if repo_dir.exists() and (repo_dir / ".git").exists():
                try:
                    yml_files = scan_yml_files(repo_dir, local_path)
                except Exception:
                    yml_files = []
                # 取 git 目录修改时间作为 last_sync 近似值
                try:
                    import datetime as _dt
                    mtime = (repo_dir / ".git").stat().st_mtime
                    dt = _dt.datetime.fromtimestamp(mtime, _dt.timezone(_dt.timedelta(hours=8)))
                    last_sync = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    last_sync = "已同步"
            else:
                status = "pending"

            repo_info = RepoInfo(
                name=name,
                url=repo_url,
                branch=branch,
                local_path=local_path,
                yml_files=yml_files,
                last_sync=last_sync,
                status=status,
                repo_dir_name=actual_repo_dir_name
            )
            repos_db.append(repo_info)
        _repos_loaded = True
    except Exception as e:
        print(f"从 JSON 加载仓库信息失败: {e}")

# 延迟加载仓库
def _ensure_repos_loaded():
    if not _repos_loaded:
        _load_repos_from_json()

# 初始化代理配置
proxy_config: Dict = db_get_proxy_config()

def get_requests_proxies() -> Dict[str, str]:
    proxies = {}
    http_proxy = proxy_config["http_proxy"]
    https_proxy = proxy_config["https_proxy"]
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy
    elif http_proxy:
        proxies["https"] = http_proxy
    return proxies

def get_repo_name_from_url(url: str) -> str:
    return url.rstrip('/').split('/')[-1].replace('.git', '')

def clone_or_pull_repo(repo_url: str, branch: str, local_path: str, max_retries: int = 3) -> Dict:
    repo_name = get_repo_name_from_url(repo_url)
    repo_dir = REPOS_DIR / repo_name

    env = os.environ.copy()
    http_proxy = proxy_config["http_proxy"]
    https_proxy = proxy_config["https_proxy"] or http_proxy
    if http_proxy:
        env["HTTP_PROXY"] = http_proxy
        env["http_proxy"] = http_proxy
    if https_proxy:
        env["HTTPS_PROXY"] = https_proxy
        env["https_proxy"] = https_proxy

    def is_repo_incomplete(directory: Path) -> bool:
        git_dir = directory / ".git"
        if not git_dir.exists():
            return False
        head_file = git_dir / "HEAD"
        if not head_file.exists():
            return True
        try:
            result = subprocess.run(
                ["git", "status", "--short"],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=30,
                env=env
            )
            return result.returncode != 0
        except Exception:
            return True

    def do_clone(directory: Path) -> Dict:
        for attempt in range(max_retries):
            try:
                if directory.exists():
                    shutil.rmtree(directory, ignore_errors=True)
                result = subprocess.run(
                    ["git", "clone", "-b", branch, "--depth", "1", repo_url, str(directory)],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env=env
                )
                if result.returncode == 0:
                    return {
                        "success": True,
                        "message": "仓库克隆成功",
                        "status": "active",
                        "path": str(directory)
                    }
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return {
                    "success": False,
                    "message": f"克隆失败: {result.stderr}",
                    "status": "error"
                }
            except subprocess.TimeoutExpired:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return {
                    "success": False,
                    "message": "克隆超时",
                    "status": "error"
                }
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return {
                    "success": False,
                    "message": f"克隆失败: {str(e)}",
                    "status": "error"
                }
        return {"success": False, "message": "克隆失败", "status": "error"}

    def do_pull(directory: Path) -> Dict:
        for attempt in range(max_retries):
            try:
                result = subprocess.run(
                    ["git", "pull", "origin", branch],
                    cwd=directory,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=env
                )
                if result.returncode == 0:
                    return {
                        "success": True,
                        "message": "仓库更新成功",
                        "status": "active",
                        "path": str(directory)
                    }
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return {
                    "success": False,
                    "message": f"更新失败: {result.stderr}",
                    "status": "error"
                }
            except subprocess.TimeoutExpired:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return {
                    "success": False,
                    "message": "更新超时",
                    "status": "error"
                }
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return {
                    "success": False,
                    "message": f"更新失败: {str(e)}",
                    "status": "error"
                }
        return {"success": False, "message": "更新失败", "status": "error"}

    try:
        if repo_dir.exists():
            if is_repo_incomplete(repo_dir):
                print(f"仓库 {repo_name} 不完整，重新克隆...")
                return do_clone(repo_dir)
            result = do_pull(repo_dir)
            if not result["success"]:
                print(f"仓库 {repo_name} 更新失败，尝试重新克隆...")
                return do_clone(repo_dir)
            return result
        else:
            return do_clone(repo_dir)
    except Exception as e:
        return {
            "success": False,
            "message": f"操作失败: {str(e)}",
            "status": "error"
        }

def init_default_repos():
    """仅当 repos.json 为空时写入默认 4 个仓库配置（不执行 clone）"""
    existing = load_repos_config()
    if existing and len(existing) > 0:
        # JSON 已有内容，不做处理；但缺项时补齐（比如之前有用户手动清空某个仓库）
        need_save = False
        existing_names = {r.get("name") for r in existing}
        for default in DEFAULT_REPOS_JSON:
            if default["name"] not in existing_names:
                existing.append(default)
                need_save = True
        if need_save:
            save_repos_config(existing)
        return

    # JSON 为空时写入默认配置
    save_repos_config(DEFAULT_REPOS_JSON)

def scan_yml_files(repo_dir: Path, local_path: str = "") -> List[YmlFile]:
    yml_files = []
    
    scan_dir = repo_dir
    if local_path:
        scan_dir = repo_dir / local_path
        if not scan_dir.exists():
            scan_dir = repo_dir
    
    for yml_path in scan_dir.rglob("*.yml"):
        try:
            with open(yml_path, 'r', encoding='utf-8') as f:
                content = f.read()
                yml_files.append(YmlFile(
                    name=yml_path.name,
                    path=str(yml_path.relative_to(repo_dir)),
                    content=content
                ))
        except Exception:
            continue
            
    for yml_path in scan_dir.rglob("*.yaml"):
        try:
            with open(yml_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if yml_path.name not in [y.name for y in yml_files]:
                    yml_files.append(YmlFile(
                        name=yml_path.name,
                        path=str(yml_path.relative_to(repo_dir)),
                        content=content
                    ))
        except Exception:
            continue
    
    return yml_files

def get_all_repos() -> List[Dict]:
    _ensure_repos_loaded()
    current_repo = get_setting("current_repo", "")
    return [
        {
            "name": repo.name,
            "url": repo.url,
            "branch": repo.branch,
            "local_path": repo.local_path,
            "yml_count": len(repo.yml_files),
            "last_sync": repo.last_sync,
            "status": repo.status,
            "is_current": repo.name == current_repo
        }
        for repo in repos_db
    ]

def add_repo(repo_url: str, branch: str, local_path: str, name: Optional[str] = None) -> Dict:
    _ensure_repos_loaded()
    repo_name = name if name else get_repo_name_from_url(repo_url)
    actual_repo_dir_name = get_repo_name_from_url(repo_url)
    
    for repo in repos_db:
        if repo.name == repo_name:
            log_service.warning(f"仓库已存在: {repo_name}", 'system')
            return {"success": False, "message": "仓库已存在", "status": "error"}
    
    result = clone_or_pull_repo(repo_url, branch, local_path)
    
    if not result["success"]:
        log_service.error(f"仓库添加失败: {repo_name} - {result.get('message', '未知错误')}", 'system')
        return result
    
    repo_dir = Path(result["path"])
    yml_files = scan_yml_files(repo_dir, local_path)
    
    repo_info = RepoInfo(
        name=repo_name,
        url=repo_url,
        branch=branch,
        local_path=local_path,
        yml_files=yml_files,
        last_sync="刚刚",
        status="active",
        repo_dir_name=actual_repo_dir_name
    )
    repos_db.append(repo_info)
    
    # 保存 4 字段到 JSON
    try:
        current_json = load_repos_config()
        current_json.append({
            "name": repo_name,
            "repo_url": repo_url,
            "branch": branch,
            "local_path": local_path
        })
        save_repos_config(current_json)
    except Exception as e:
        print(f"保存仓库到 repos.json 失败: {e}")
    
    log_service.success(f"仓库添加成功: {repo_name} (发现 {len(yml_files)} 个 YML 文件)", 'system')
    
    return {
        "success": True,
        "message": f"仓库添加成功，发现 {len(yml_files)} 个 YML 文件",
        "data": {
            "name": repo_name,
            "yml_count": len(yml_files),
            "yml_files": [
                {"name": f.name, "path": f.path}
                for f in yml_files
            ]
        }
    }

def sync_repo(repo_name: str) -> Dict:
    _ensure_repos_loaded()
    for i, repo in enumerate(repos_db):
        if repo.name == repo_name:
            result = clone_or_pull_repo(repo.url, repo.branch, repo.local_path)
            
            if result["success"]:
                repo_dir = Path(result["path"])
                repo.yml_files = scan_yml_files(repo_dir, repo.local_path)
                repo.status = "active"
                repo.last_sync = "刚刚"
                repos_db[i] = repo
                
                # 注：yml_files/last_sync/status 为动态字段，不写入 JSON；4 字段不变无需写入
                
                log_service.info(f"仓库同步成功: {repo_name} (发现 {len(repo.yml_files)} 个 YML 文件)", 'system')
                
                return {
                    "success": True,
                    "message": f"同步成功，发现 {len(repo.yml_files)} 个 YML 文件",
                    "data": {
                        "yml_count": len(repo.yml_files),
                        "yml_files": [
                            {"name": f.name, "path": f.path}
                            for f in repo.yml_files
                        ]
                    }
                }
            else:
                log_service.error(f"仓库同步失败: {repo_name} - {result.get('message', '未知错误')}", 'system')
                return result
    
    log_service.warning(f"仓库不存在: {repo_name}", 'system')
    return {"success": False, "message": "仓库不存在", "status": "error"}

def get_repo(repo_name: str) -> Optional[Dict]:
    _ensure_repos_loaded()
    for repo in repos_db:
        if repo.name == repo_name:
            return {
                "name": repo.name,
                "url": repo.url,
                "branch": repo.branch,
                "local_path": repo.local_path,
                "yml_files": [
                    {"name": f.name, "path": f.path}
                    for f in repo.yml_files
                ],
                "last_sync": repo.last_sync,
                "status": repo.status
            }
    return None

def get_yml_content(repo_name: str, file_path: str) -> Optional[Dict]:
    _ensure_repos_loaded()
    for repo in repos_db:
        if repo.name == repo_name:
            actual_repo_dir_name = repo.repo_dir_name if repo.repo_dir_name else get_repo_name_from_url(repo.url)
            repo_dir = REPOS_DIR / actual_repo_dir_name
            for yml_file in repo.yml_files:
                if yml_file.path == file_path or yml_file.name == file_path:
                    file_full_path = repo_dir / yml_file.path
                    if file_full_path.exists():
                        import os
                        mtime = file_full_path.stat().st_mtime
                        from datetime import datetime, timezone, timedelta
                        last_modified = datetime.fromtimestamp(mtime, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        last_modified = repo.last_sync or '未知'
                    
                    return {
                        "name": yml_file.name,
                        "path": yml_file.path,
                        "content": yml_file.content,
                        "last_modified": last_modified
                    }
    return None

def get_repo_files(repo_name: str) -> Optional[List[Dict]]:
    _ensure_repos_loaded()
    for repo in repos_db:
        if repo.name == repo_name:
            return [
                {"name": f.name, "path": f.path}
                for f in repo.yml_files
            ]
    return None

def save_file_content(repo_name: str, file_name: str, content: str) -> bool:
    _ensure_repos_loaded()
    for repo in repos_db:
        if repo.name == repo_name:
            actual_repo_dir_name = repo.repo_dir_name if repo.repo_dir_name else get_repo_name_from_url(repo.url)
            repo_dir = REPOS_DIR / actual_repo_dir_name
            for i, yml_file in enumerate(repo.yml_files):
                if yml_file.name == file_name:
                    file_path = repo_dir / yml_file.path
                    try:
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(content)
                        repo.yml_files[i].content = content
                        # 注：yml_files 是动态内存缓存，不写入 JSON/DB
                        return True
                    except Exception as e:
                        print(f"保存文件失败: {e}")
                        return False
    return False

def delete_repo(repo_name: str) -> bool:
    _ensure_repos_loaded()
    for i, repo in enumerate(repos_db):
        if repo.name == repo_name:
            repos_db.pop(i)
            # 从 JSON 删除
            try:
                current_json = load_repos_config()
                current_json = [r for r in current_json if r.get("name") != repo_name]
                save_repos_config(current_json)
            except Exception as e:
                print(f"从 repos.json 删除仓库失败: {e}")
            log_service.warning(f"仓库已删除: {repo_name}", 'system')
            return True
    log_service.warning(f"删除仓库失败: {repo_name} - 仓库不存在", 'system')
    return False

def deploy_yml(repo_name: str, file_path: str) -> Generator[dict, None, None]:
    """流式部署 YML，逐条 yield 事件 dict。

    事件结构:
      {"type": "log", "level": "info", "stage": "start|pull|up", "message": "...", "ts": ISO时间}
      {"type": "progress", "stage": "pull|up", "percent": 0-100, "detail": "...",
       "elapsed_sec": float, "eta_sec": float, "ts": ISO时间}
      {"type": "done", "success": bool, "message": "...", "data": {...},
       "ts": ISO时间, "elapsed_sec": 本阶段总耗时}
    """
    from .database import add_deployment

    for repo in repos_db:
        if repo.name == repo_name:
            for yml_file in repo.yml_files:
                if yml_file.path == file_path or yml_file.name == file_path:
                    # 使用实际的仓库目录名称，而不是自定义名称
                    actual_repo_dir_name = repo.repo_dir_name if repo.repo_dir_name else get_repo_name_from_url(repo.url)
                    repo_dir = REPOS_DIR / actual_repo_dir_name
                    yml_full_path = repo_dir / yml_file.path

                    # 设置环境变量（包含代理配置 + 强制 plain 进度）
                    env = os.environ.copy()
                    http_proxy = proxy_config["http_proxy"]
                    https_proxy = proxy_config["https_proxy"] or http_proxy
                    if http_proxy:
                        env["HTTP_PROXY"] = http_proxy
                        env["http_proxy"] = http_proxy
                    if https_proxy:
                        env["HTTPS_PROXY"] = https_proxy
                        env["https_proxy"] = https_proxy
                    # 非 TTY 下 docker-compose/docker 默认会禁用交互进度条，强制 plain 文本输出
                    # 否则子进程管道里什么都不吐，页面看起来就是"卡住"
                    # 注：只在 env 里塞 COMPOSE_PROGRESS_TYPE=plain，不在命令行加 `--progress plain`
                    #     因为 docker-compose v1 (1.x) 会报 "unknown flag: --progress" 直接失败
                    env["COMPOSE_PROGRESS_TYPE"] = "plain"
                    env["PROGRESS_NO_TRUNC"] = "1"
                    env["PYTHONUNBUFFERED"] = "1"

                    deployment_logs = []
                    deploy_started_at = time.time()

                    def log(msg, stage="start"):
                        deployment_logs.append(msg)
                        return {"type": "log", "level": "info", "stage": stage, "message": msg, "ts": _now_iso()}

                    try:
                        yield log(f"[部署开始] 正在处理文件: {yml_file.name}")
                        yield log(f"[部署开始] 文件路径: {yml_full_path}")

                        # 拉取镜像（实时流式输出）
                        pull_returncode = yield from _stream_command(
                            ["docker-compose", "-f", str(yml_full_path), "pull"],
                            env, timeout=300, stage="pull", label="[镜像拉取]",
                            deployment_logs=deployment_logs,
                        )
                        if pull_returncode != 0:
                            total_elapsed = round(time.time() - deploy_started_at, 1)
                            log_service.error(f"镜像拉取失败: {yml_file.name} - pull 阶段返回码 {pull_returncode}", 'deploy')
                            yield {
                                "type": "done",
                                "success": False,
                                "message": f"部署失败: 拉取镜像阶段返回码 {pull_returncode}",
                                "data": {
                                    "repo_name": repo_name,
                                    "file_name": yml_file.name,
                                    "file_path": yml_file.path,
                                    "status": "failed",
                                    "detailed_logs": deployment_logs,
                                },
                                "ts": _now_iso(),
                                "elapsed_sec": total_elapsed,
                            }
                            return

                        yield log("[部署阶段] 启动容器...", stage="up")

                        # 启动容器（实时流式输出）。注意 docker-compose up 在镜像缺失时会自行触发 pull，
                        # 所以这里的空闲超时要和 pull 阶段同等宽松，避免慢网下长下载误杀。
                        up_returncode = yield from _stream_command(
                            ["docker-compose", "-f", str(yml_full_path), "up", "-d"],
                            env, timeout=300, stage="up", label="[启动日志]",
                            deployment_logs=deployment_logs,
                        )

                        if up_returncode == 0:
                            container_id = None
                            container_name = None

                            try:
                                ps_result = subprocess.run(
                                    ["docker-compose", "-f", str(yml_full_path), "ps", "-q"],
                                    capture_output=True,
                                    text=True,
                                    timeout=30
                                )
                                if ps_result.returncode == 0 and ps_result.stdout.strip():
                                    container_id = ps_result.stdout.strip().split('\n')[0]

                                ps_full_result = subprocess.run(
                                    ["docker-compose", "-f", str(yml_full_path), "ps"],
                                    capture_output=True,
                                    text=True,
                                    timeout=30
                                )
                                if ps_full_result.returncode == 0:
                                    lines = ps_full_result.stdout.strip().split('\n')
                                    if len(lines) > 1:
                                        container_name = lines[1].split()[0]
                            except Exception:
                                pass

                            add_deployment(repo_name, yml_file.name, container_id, container_name, 'deployed', f'部署成功')

                            success_log1 = f"[部署成功] 容器ID: {container_id}"
                            success_log2 = f"[部署成功] 容器名称: {container_name}"
                            deployment_logs.append(success_log1)
                            deployment_logs.append(success_log2)
                            yield {"type": "log", "level": "success", "stage": "done", "message": success_log1, "ts": _now_iso()}
                            yield {"type": "log", "level": "success", "stage": "done", "message": success_log2, "ts": _now_iso()}

                            log_service.success(f"容器部署成功: {yml_file.name} (容器名: {container_name})", 'deploy', deployment_logs)

                            total_elapsed = round(time.time() - deploy_started_at, 1)
                            yield {
                                "type": "done",
                                "success": True,
                                "message": f"部署成功: {yml_file.name}",
                                "data": {
                                    "repo_name": repo_name,
                                    "file_name": yml_file.name,
                                    "file_path": yml_file.path,
                                    "status": "deployed",
                                    "detailed_logs": deployment_logs,
                                    "container_id": container_id,
                                    "container_name": container_name
                                },
                                "ts": _now_iso(),
                                "elapsed_sec": total_elapsed,
                            }
                            return
                        else:
                            total_elapsed = round(time.time() - deploy_started_at, 1)
                            log_service.error(f"容器部署失败: {yml_file.name} - up 阶段返回码 {up_returncode}", 'deploy')
                            yield {
                                "type": "done",
                                "success": False,
                                "message": f"部署失败: up 阶段返回码 {up_returncode}",
                                "data": {
                                    "repo_name": repo_name,
                                    "file_name": yml_file.name,
                                    "file_path": yml_file.path,
                                    "status": "failed",
                                    "detailed_logs": deployment_logs
                                },
                                "ts": _now_iso(),
                                "elapsed_sec": total_elapsed,
                            }
                            return
                    except subprocess.TimeoutExpired:
                        total_elapsed = round(time.time() - deploy_started_at, 1)
                        log_service.error(f"容器部署超时: {yml_file.name}", 'deploy')
                        yield {
                            "type": "done",
                            "success": False,
                            "message": "部署超时",
                            "data": {
                                "repo_name": repo_name,
                                "file_name": yml_file.name,
                                "file_path": yml_file.path,
                                "status": "timeout",
                                "detailed_logs": deployment_logs
                            },
                            "ts": _now_iso(),
                            "elapsed_sec": total_elapsed,
                        }
                        return
                    except FileNotFoundError:
                        total_elapsed = round(time.time() - deploy_started_at, 1)
                        log_service.error(f"docker-compose 命令未找到: {yml_file.name}", 'deploy')
                        yield {
                            "type": "done",
                            "success": False,
                            "message": "docker-compose 命令未找到，请确保已安装 Docker Compose",
                            "data": {
                                "repo_name": repo_name,
                                "file_name": yml_file.name,
                                "file_path": yml_file.path,
                                "status": "error",
                                "detailed_logs": deployment_logs
                            },
                            "ts": _now_iso(),
                            "elapsed_sec": total_elapsed,
                        }
                        return
                    except Exception as e:
                        total_elapsed = round(time.time() - deploy_started_at, 1)
                        log_service.error(f"容器部署异常: {yml_file.name} - {str(e)}", 'deploy')
                        yield {
                            "type": "done",
                            "success": False,
                            "message": f"部署异常: {str(e)}",
                            "data": {
                                "repo_name": repo_name,
                                "file_name": yml_file.name,
                                "file_path": yml_file.path,
                                "status": "error",
                                "detailed_logs": deployment_logs
                            },
                            "ts": _now_iso(),
                            "elapsed_sec": total_elapsed,
                        }
                        return
    # 未找到对应仓库/文件
    yield {
        "type": "done",
        "success": False,
        "message": "仓库或文件不存在",
        "data": {
            "repo_name": repo_name,
            "file_name": file_path,
            "file_path": file_path,
            "status": "error"
        },
        "ts": _now_iso(),
        "elapsed_sec": 0.0,
    }

def get_running_containers_count() -> int:
    try:
        result = subprocess.run(
            ["docker", "ps", "--quiet"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            return len([line for line in lines if line.strip()])
        else:
            return 0
    except subprocess.TimeoutExpired:
        return 0
    except FileNotFoundError:
        return 0
    except Exception:
        return 0

def get_all_containers() -> list:
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}|{{.CreatedAt}}|{{.Command}}"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            containers = []
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if line.strip():
                    parts = line.split('|')
                    if len(parts) >= 7:
                        container_id = parts[0].strip()
                        name = parts[1].strip()
                        image = parts[2].strip()
                        status = parts[3].strip()
                        ports = parts[4].strip()
                        created_at = parts[5].strip()
                        command = parts[6].strip()
                        
                        state = 'running' if 'Up' in status else 'exited'
                        uptime = ''
                        if 'Up' in status:
                            uptime_match = status.split('Up ')[1].split(' ')[0]
                            uptime = uptime_match
                        
                        ports_list = []
                        if ports != '<none>':
                            ports_list = [p.strip() for p in ports.split(',')]
                        
                        containers.append({
                            'id': container_id,
                            'name': name,
                            'image': image,
                            'state': state,
                            'status': status,
                            'uptime': uptime,
                            'ports': ports_list,
                            'created_at': created_at,
                            'command': command
                        })
            return containers
        else:
            return []
    except subprocess.TimeoutExpired:
        return []
    except FileNotFoundError:
        return []
    except Exception:
        return []

def get_container_by_id(container_id: str) -> dict:
    import datetime
    try:
        result = subprocess.run(
            ["docker", "inspect", container_id],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)[0]
            
            state = data['State']['Status']
            uptime = ''
            if state == 'running' and data['State']['StartedAt']:
                started_at = datetime.datetime.fromisoformat(data['State']['StartedAt'].replace('Z', '+00:00'))
                now = datetime.datetime.now(datetime.timezone.utc)
                delta = now - started_at
                
                days = delta.days
                hours = delta.seconds // 3600
                minutes = (delta.seconds % 3600) // 60
                
                if days > 0:
                    uptime = f'{days}天{hours}小时{minutes}分钟'
                elif hours > 0:
                    uptime = f'{hours}小时{minutes}分钟'
                else:
                    uptime = f'{minutes}分钟'
            
            ports = []
            try:
                network_ports = data.get('NetworkSettings', {}).get('Ports', [])
                if isinstance(network_ports, list):
                    for port in network_ports:
                        if isinstance(port, dict) and port.get('PublicPort'):
                            ports.append(f"{port['PublicPort']}->{port['PrivatePort']}/{port['Type']}")
                elif isinstance(network_ports, dict):
                    for private_port, bindings in network_ports.items():
                        if bindings and isinstance(bindings, list):
                            for binding in bindings:
                                if binding and binding.get('HostPort'):
                                    ports.append(f"{binding['HostPort']}->{private_port}")
            except Exception:
                ports = []
            
            created_at = data['Created']
            if created_at:
                created_dt = datetime.datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                created_dt = created_dt.astimezone(datetime.timezone(datetime.timedelta(hours=8)))
                created_at = created_dt.strftime('%Y-%m-%d %H:%M:%S')
            
            return {
                'id': data['Id'],
                'name': data['Name'].lstrip('/'),
                'image': data['Config']['Image'],
                'state': state,
                'status': data['State']['Status'],
                'uptime': uptime,
                'ports': ports,
                'created_at': created_at,
                'command': ' '.join(data['Config']['Cmd']) if data['Config']['Cmd'] else ''
            }
        else:
            return None
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        return None
    except Exception:
        return None

def start_container(container_id: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "start", container_id],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            log_service.success(f"容器启动成功: {container_id[:12]}", 'container')
            return True
        else:
            log_service.error(f"容器启动失败: {container_id[:12]} - {result.stderr}", 'container')
            return False
    except subprocess.TimeoutExpired:
        log_service.error(f"容器启动超时: {container_id[:12]}", 'container')
        return False
    except FileNotFoundError:
        return False
    except Exception:
        return False

def stop_container(container_id: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "stop", container_id],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            log_service.warning(f"容器已停止: {container_id[:12]}", 'container')
            return True
        else:
            log_service.error(f"容器停止失败: {container_id[:12]} - {result.stderr}", 'container')
            return False
    except subprocess.TimeoutExpired:
        log_service.error(f"容器停止超时: {container_id[:12]}", 'container')
        return False
    except FileNotFoundError:
        return False
    except Exception:
        return False

def restart_container(container_id: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "restart", container_id],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            log_service.success(f"容器重启成功: {container_id[:12]}", 'container')
            return True
        else:
            log_service.error(f"容器重启失败: {container_id[:12]} - {result.stderr}", 'container')
            return False
    except subprocess.TimeoutExpired:
        log_service.error(f"容器重启超时: {container_id[:12]}", 'container')
        return False
    except FileNotFoundError:
        return False
    except Exception:
        return False

def remove_container(container_id: str, force: bool = False) -> bool:
    try:
        cmd = ["docker", "rm", container_id]
        if force:
            cmd.insert(2, "-f")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            log_service.warning(f"容器已删除: {container_id[:12]}", 'container')
            return True
        else:
            log_service.error(f"容器删除失败: {container_id[:12]} - {result.stderr}", 'container')
            return False
    except subprocess.TimeoutExpired:
        log_service.error(f"容器删除超时: {container_id[:12]}", 'container')
        return False
    except FileNotFoundError:
        return False
    except Exception:
        return False

def get_all_images(use_cache=True) -> list:
    """获取所有镜像列表，支持缓存"""
    # 如果使用缓存，尝试从数据库读取
    if use_cache:
        try:
            cached = get_images_cache()
            if cached:
                return cached
        except Exception as e:
            print(f"读取镜像缓存失败: {e}")
    
    # 从 Docker 获取
    try:
        result = subprocess.run(
            ["docker", "images", "--filter", "dangling=false", "--format", "{{.ID}}|{{.Repository}}|{{.Tag}}|{{.Size}}|{{.CreatedSince}}|{{.CreatedAt}}"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            images = []
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if line.strip():
                    parts = line.split('|')
                    if len(parts) >= 6:
                        image_id = parts[0].strip()
                        repository = parts[1].strip()
                        tag = parts[2].strip()
                        size = parts[3].strip()
                        created_since = parts[4].strip()
                        created_at = parts[5].strip()
                        
                        repo_tags = []
                        if repository != '<none>':
                            repo_tags.append(f"{repository}:{tag}")
                        
                        images.append({
                            'id': image_id,
                            'name': repository if repository != '<none>' else 'untagged',
                            'tag': tag,
                            'repo_tags': repo_tags,
                            'size': parse_size(size),
                            'created_since': created_since,
                            'created_at': created_at
                        })
            
            # 更新缓存
            try:
                update_images_cache(images)
            except Exception as e:
                print(f"更新镜像缓存失败: {e}")
            
            return images
        else:
            return []
    except subprocess.TimeoutExpired:
        return []
    except FileNotFoundError:
        return []
    except Exception:
        return []

def refresh_images_cache() -> list:
    """强制刷新镜像缓存"""
    return get_all_images(use_cache=False)

def parse_size(size_str: str) -> int:
    try:
        size_str = size_str.strip()
        if size_str.endswith('GB'):
            return int(float(size_str[:-2]) * 1024 * 1024 * 1024)
        elif size_str.endswith('MB'):
            return int(float(size_str[:-2]) * 1024 * 1024)
        elif size_str.endswith('KB'):
            return int(float(size_str[:-2]) * 1024)
        elif size_str.endswith('B'):
            return int(size_str[:-1])
        return 0
    except Exception:
        return 0

def delete_image(image_id: str) -> Dict:
    try:
        result = subprocess.run(
            ["docker", "rmi", "-f", image_id],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            # 刷新缓存
            try:
                refresh_images_cache()
            except Exception as e:
                print(f"刷新镜像缓存失败: {e}")
            log_service.warning(f"镜像已删除: {image_id}", 'image')
            return {"success": True, "message": "镜像删除成功"}
        else:
            log_service.error(f"镜像删除失败: {image_id} - {result.stderr}", 'image')
            return {"success": False, "message": f"删除失败: {result.stderr}"}
    except subprocess.TimeoutExpired:
        log_service.error(f"镜像删除超时: {image_id}", 'image')
        return {"success": False, "message": "删除操作超时"}
    except FileNotFoundError:
        return {"success": False, "message": "Docker命令不可用"}
    except Exception as e:
        log_service.error(f"镜像删除异常: {image_id} - {str(e)}", 'image')
        return {"success": False, "message": f"删除失败: {str(e)}"}

def search_dockerhub_images(query: str) -> list:
    try:
        proxies = get_requests_proxies()
        url = f"https://hub.docker.com/v2/search/repositories?query={query}&page_size=20"
        response = requests.get(url, timeout=10, proxies=proxies)
        if response.status_code == 200:
            data = response.json()
            results = []
            for result in data.get('results', []):
                name = result.get('name') or result.get('repo_name')
                
                if not name:
                    continue
                    
                description = result.get('description') or result.get('short_description') or '暂无描述'
                is_official = result.get('is_official', False)
                is_automated = result.get('is_automated', False)
                
                tags = ['latest']
                try:
                    tags_url = f"https://hub.docker.com/v2/repositories/{name}/tags?page_size=10"
                    tags_response = requests.get(tags_url, timeout=5, proxies=proxies)
                    if tags_response.status_code == 200:
                        tags_data = tags_response.json()
                        tag_results = tags_data.get('results', [])
                        tags = [tag.get('name') for tag in tag_results if tag.get('name')][:5]
                except Exception:
                    tags = ['latest']
                
                results.append({
                    'name': name,
                    'description': description if description else '暂无描述',
                    'is_official': is_official,
                    'is_automated': is_automated,
                    'tags': tags if tags else ['latest']
                })
            return results
        return []
    except requests.exceptions.RequestException:
        return []
    except Exception:
        return []

def pull_image(image_name: str) -> Generator[dict, None, None]:
    """流式拉取镜像，逐条 yield 事件 dict。

    事件结构:
      {"type": "log", ..., "stage": "pull", "message": "...", "ts": ISO时间}
      {"type": "progress", "stage": "pull", "percent": 0-100, "detail": "...",
       "elapsed_sec": float, "eta_sec": float, "ts": ISO时间}
      {"type": "done", "success": bool, "message": "...",
       "data": {"image_name": ..., "logs": [...], "ts": ISO时间, "elapsed_sec": 总耗时}}
    """
    try:
        env = os.environ.copy()
        http_proxy = proxy_config["http_proxy"]
        https_proxy = proxy_config["https_proxy"] or http_proxy
        if http_proxy:
            env["HTTP_PROXY"] = http_proxy
            env["http_proxy"] = http_proxy
        if https_proxy:
            env["HTTPS_PROXY"] = https_proxy
            env["https_proxy"] = https_proxy
        # 非 TTY 下 docker pull 默认会压缩进度输出，强制 plain 文本让管道能读到每一行
        env["PROGRESS_NO_TRUNC"] = "1"
        env["PYTHONUNBUFFERED"] = "1"

        pull_logs = []
        started_at = time.time()

        # 注：docker 19.03+ 才支持 `--progress plain`，旧版会报 unknown flag，
        # 所以这里不加命令行参数，仅依赖 env PROGRESS_NO_TRUNC=1 让输出更稳定。
        returncode = yield from _stream_command(
            ["docker", "pull", image_name],
            env, timeout=300, stage="pull", label="[镜像拉取]",
            deployment_logs=pull_logs,
        )
        total_elapsed = round(time.time() - started_at, 1)

        if returncode == 0:
            try:
                refresh_images_cache()
            except Exception as e:
                print(f"刷新镜像缓存失败: {e}")
            log_service.success(f"镜像拉取成功: {image_name}", 'image')
            yield {
                "type": "done",
                "success": True,
                "message": f"镜像拉取成功: {image_name}",
                "data": {"image_name": image_name, "logs": pull_logs},
                "ts": _now_iso(),
                "elapsed_sec": total_elapsed,
            }
        else:
            log_service.error(f"镜像拉取失败: {image_name} - 返回码 {returncode}", 'image')
            yield {
                "type": "done",
                "success": False,
                "message": f"拉取失败: 返回码 {returncode}",
                "data": {"image_name": image_name, "logs": pull_logs},
                "ts": _now_iso(),
                "elapsed_sec": total_elapsed,
            }
    except subprocess.TimeoutExpired:
        log_service.error(f"镜像拉取超时: {image_name}", 'image')
        yield {"type": "done", "success": False, "message": "拉取操作超时",
               "data": {"image_name": image_name}, "ts": _now_iso(), "elapsed_sec": 0.0}
    except FileNotFoundError:
        yield {"type": "done", "success": False, "message": "Docker命令不可用",
               "data": {"image_name": image_name}, "ts": _now_iso(), "elapsed_sec": 0.0}
    except Exception as e:
        yield {"type": "done", "success": False, "message": f"拉取失败: {str(e)}",
               "data": {"image_name": image_name}, "ts": _now_iso(), "elapsed_sec": 0.0}

import requests
import time

def test_connectivity(url: str, timeout: int = 10) -> Dict:
    """测试网络连通性"""
    try:
        proxies = get_requests_proxies()
        start_time = time.time()
        response = requests.get(url, timeout=timeout, verify=True, proxies=proxies)
        latency = int((time.time() - start_time) * 1000)
        
        if response.status_code == 200:
            return {
                "success": True,
                "url": url,
                "status": "reachable",
                "latency": latency,
                "status_code": response.status_code,
                "message": "连接成功"
            }
        else:
            return {
                "success": False,
                "url": url,
                "status": "unreachable",
                "latency": latency,
                "status_code": response.status_code,
                "message": f"连接失败，HTTP状态码: {response.status_code}"
            }
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "url": url,
            "status": "timeout",
            "latency": timeout * 1000,
            "message": "连接超时"
        }
    except requests.exceptions.SSLError:
        return {
            "success": False,
            "url": url,
            "status": "ssl_error",
            "latency": 0,
            "message": "SSL证书错误"
        }
    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "url": url,
            "status": "connection_error",
            "latency": 0,
            "message": "连接失败，无法建立连接"
        }
    except Exception as e:
        return {
            "success": False,
            "url": url,
            "status": "error",
            "latency": 0,
            "message": f"测试异常: {str(e)}"
        }

def test_all_connectivity() -> Dict:
    """测试所有预设的网络连接"""
    targets = [
        {"name": "GitHub", "url": "https://github.com/"},
        {"name": "Docker Hub", "url": "https://hub.docker.com/"}
    ]
    
    results = []
    for target in targets:
        result = test_connectivity(target["url"])
        result["name"] = target["name"]
        results.append(result)
    
    all_successful = all(r["success"] for r in results)
    
    return {
        "success": all_successful,
        "results": results,
        "total_tests": len(results),
        "successful_tests": sum(1 for r in results if r["success"])
    }

def get_proxy_config() -> Dict:
    """获取当前代理配置"""
    return proxy_config.copy()

def get_current_repo() -> str:
    """获取当前系统仓库"""
    return get_setting("current_repo", "")

def set_current_repo(repo_name: str) -> bool:
    """设置当前系统仓库"""
    set_setting("current_repo", repo_name)
    return True

def set_proxy_config(http_proxy: str = "", https_proxy: str = "") -> Dict:
    """设置代理配置"""
    global proxy_config
    
    # 验证代理格式
    def validate_proxy(url: str) -> bool:
        if not url:
            return True
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.scheme in ('http', 'https') and parsed.hostname and parsed.port
        except:
            return False
    
    if http_proxy and not validate_proxy(http_proxy):
        log_service.error(f"代理配置失败: HTTP代理格式不正确", 'system')
        return {"success": False, "message": "HTTP代理格式不正确，请使用 http://ip:port 格式"}
    
    if https_proxy and not validate_proxy(https_proxy):
        log_service.error(f"代理配置失败: HTTPS代理格式不正确", 'system')
        return {"success": False, "message": "HTTPS代理格式不正确，请使用 https://ip:port 格式"}
    
    proxy_config["http_proxy"] = http_proxy.strip() if http_proxy else ""
    proxy_config["https_proxy"] = https_proxy.strip() if https_proxy else ""
    
    # 保存到数据库
    try:
        db_set_proxy_config(proxy_config["http_proxy"], proxy_config["https_proxy"])
    except Exception as e:
        print(f"保存代理配置到数据库失败: {e}")
    
    log_service.info(f"代理配置已更新: HTTP={http_proxy or '无'}, HTTPS={https_proxy or '无'}", 'system')
    
    return {"success": True, "message": "代理配置已保存"}

def get_docker_info():
    docker_version = ""
    docker_compose_version = ""
    
    try:
        result = subprocess.run(["docker", "--version"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            docker_version = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        docker_version = "Docker 未安装或不可用"
    
    try:
        result = subprocess.run(["docker-compose", "--version"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            docker_compose_version = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        docker_compose_version = "Docker Compose 未安装或不可用"
    
    return {
        "docker_version": docker_version,
        "docker_compose_version": docker_compose_version
    }

def get_host_system_info():
    info = {
        "cpu_usage": "0%",
        "memory_total": "0 MB",
        "memory_used": "0 MB",
        "memory_usage": "0%",
        "disk_total": "0 GB",
        "disk_used": "0 GB",
        "disk_usage": "0%",
        "os_version": "未知",
        "network_info": []
    }
    
    try:
        result = subprocess.run(["cat", "/proc/stat"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if line.startswith('cpu '):
                    parts = line.split()
                    total = sum(int(p) for p in parts[1:])
                    idle = int(parts[4])
                    usage = ((total - idle) / total) * 100
                    info["cpu_usage"] = f"{usage:.1f}%"
                    break
    except Exception:
        try:
            result = subprocess.run(["ps", "-aux"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                info["cpu_usage"] = "获取中..."
        except Exception:
            info["cpu_usage"] = "无法获取"
    
    try:
        result = subprocess.run(["cat", "/proc/meminfo"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            mem_total = 0
            mem_available = 0
            for line in result.stdout.strip().split('\n'):
                if line.startswith('MemTotal:'):
                    mem_total = int(line.split()[1]) // 1024
                elif line.startswith('MemAvailable:'):
                    mem_available = int(line.split()[1]) // 1024
            if mem_total > 0:
                mem_used = mem_total - mem_available
                info["memory_total"] = f"{mem_total} MB"
                info["memory_used"] = f"{mem_used} MB"
                info["memory_usage"] = f"{(mem_used / mem_total * 100):.1f}%"
    except Exception:
        try:
            result = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if line.startswith('Mem:'):
                        parts = line.split()
                        info["memory_total"] = f"{parts[1]} MB"
                        info["memory_used"] = f"{parts[2]} MB"
                        info["memory_usage"] = f"{(int(parts[2]) / int(parts[1]) * 100):.1f}%"
                        break
        except Exception:
            pass
    
    try:
        result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                parts = lines[1].split()
                info["disk_total"] = parts[1]
                info["disk_used"] = parts[2]
                info["disk_usage"] = parts[4].replace('%', '') + '%'
    except Exception:
        try:
            result = subprocess.run(["df", "-H", "/"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if len(lines) >= 2:
                    parts = lines[1].split()
                    info["disk_total"] = parts[1]
                    info["disk_used"] = parts[2]
                    info["disk_usage"] = parts[4].replace('%', '') + '%'
        except Exception:
            pass
    
    try:
        result = subprocess.run(["cat", "/etc/os-release"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            os_info = {}
            for line in result.stdout.strip().split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    os_info[key] = value.strip('"')
            info["os_version"] = os_info.get('PRETTY_NAME', os_info.get('NAME', '未知'))
    except Exception:
        try:
            result = subprocess.run(["uname", "-a"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                info["os_version"] = result.stdout.strip()
        except Exception:
            pass
    
    try:
        result = subprocess.run(["ip", "addr"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            interfaces = []
            current_iface = None
            for line in result.stdout.strip().split('\n'):
                if line.startswith(' '):
                    if current_iface and 'inet ' in line:
                        ip = line.split('inet ')[1].split('/')[0]
                        if not ip.startswith('127.'):
                            current_iface["ip"] = ip
                elif ':' in line:
                    if current_iface and current_iface.get("ip"):
                        interfaces.append(current_iface)
                    name = line.split(':')[1].strip()
                    current_iface = {"name": name, "ip": ""}
            if current_iface and current_iface.get("ip"):
                interfaces.append(current_iface)
            info["network_info"] = interfaces
    except Exception:
        try:
            result = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                interfaces = []
                current_iface = None
                for line in result.stdout.strip().split('\n'):
                    if line.strip() and not line.startswith(' '):
                        if current_iface and current_iface.get("ip"):
                            interfaces.append(current_iface)
                        name = line.split(':')[0].strip()
                        current_iface = {"name": name, "ip": ""}
                    elif current_iface and 'inet ' in line:
                        parts = line.split()
                        for i, part in enumerate(parts):
                            if part == 'inet' and i + 1 < len(parts):
                                ip = parts[i + 1]
                                if not ip.startswith('127.'):
                                    current_iface["ip"] = ip
                                break
                if current_iface and current_iface.get("ip"):
                    interfaces.append(current_iface)
                info["network_info"] = interfaces
        except Exception:
            pass
    
    return info

def get_latest_dockerhub_version(repo_name: str) -> Optional[str]:
    try:
        proxies = get_requests_proxies()
        url = f"https://hub.docker.com/v2/repositories/{repo_name}/tags"
        response = requests.get(url, timeout=10, proxies=proxies)
        if response.status_code == 200:
            data = response.json()
            tags = []
            for result in data.get('results', []):
                name = result.get('name')
                if name and name.startswith('v'):
                    tags.append(name)
            if tags:
                tags.sort(key=lambda v: tuple(map(int, v[1:].split('.'))))
                return tags[-1]
        return None
    except requests.exceptions.RequestException:
        return None
    except Exception:
        return None

def get_container_logs(container_id: str, tail: int = 100) -> str:
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(tail), container_id],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            return result.stdout
        else:
            return result.stderr
    except subprocess.TimeoutExpired:
        return "获取日志超时"
    except FileNotFoundError:
        return "Docker命令不可用"
    except Exception as e:
        return f"获取日志失败: {str(e)}"

# ============ 容器备份相关函数 ============

BACKUPS_DIR = Path("/app/backup")
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

VOLUMES_DIR = Path("/host/var/lib/docker/volumes")

from .database import (
    add_backup,
    get_all_backups,
    get_backups_by_container,
    delete_backup_by_id,
    get_backup_by_id as db_get_backup_by_id,
    update_backup_status
)

def get_container_mounts(container_id: str) -> list:
    """获取容器的挂载信息"""
    try:
        result = subprocess.run(
            ["docker", "inspect", container_id],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)[0]
            mounts = data.get('Mounts', [])
            return mounts
        else:
            return []
    except Exception:
        return []

def save_image(container_id: str, backup_dir: Path) -> tuple:
    """保存容器镜像"""
    try:
        container_info = get_container_by_id(container_id)
        if not container_info:
            return False, "无法获取容器信息"
        
        image_name = container_info.get('image', '')
        if not image_name:
            return False, "无法获取容器镜像名称"
        
        image_path = backup_dir / "image.tar"
        
        result = subprocess.run(
            ["docker", "save", "-o", str(image_path), image_name],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode == 0:
            return True, str(image_path)
        else:
            return False, f"保存镜像失败: {result.stderr}"
    except subprocess.TimeoutExpired:
        return False, "保存镜像超时"
    except FileNotFoundError:
        return False, "Docker命令不可用"
    except Exception as e:
        return False, f"保存镜像异常: {str(e)}"

def export_config(container_id: str, backup_dir: Path) -> tuple:
    """导出容器配置"""
    try:
        config_path = backup_dir / "container-config.json"
        
        result = subprocess.run(
            ["docker", "inspect", container_id],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            with open(config_path, 'w') as f:
                f.write(result.stdout)
            return True, str(config_path)
        else:
            return False, f"导出配置失败: {result.stderr}"
    except Exception as e:
        return False, f"导出配置异常: {str(e)}"

def pack_volumes(container_id: str, backup_dir: Path) -> tuple:
    """打包所有挂载卷"""
    try:
        mounts = get_container_mounts(container_id)
        if not mounts:
            return True, []
        
        packed_volumes = []
        
        for mount in mounts:
            mount_type = mount.get('Type', '')
            name = mount.get('Name', '')
            source = mount.get('Source', '')
            destination = mount.get('Destination', '')
            
            if not source:
                continue
            
            if mount_type == 'volume':
                volume_path = VOLUMES_DIR / name / "_data"
                if volume_path.exists():
                    volume_tar = backup_dir / f"volume-{name}.tar.gz"
                    result = subprocess.run(
                        ["tar", "-czvf", str(volume_tar), "-C", str(volume_path.parent), "_data"],
                        capture_output=True,
                        text=True,
                        timeout=300
                    )
                    if result.returncode == 0:
                        packed_volumes.append({
                            'name': name,
                            'type': 'volume',
                            'source': str(volume_path),
                            'destination': destination,
                            'file': str(volume_tar)
                        })
                    else:
                        log_service.warning(f"打包命名卷失败: {name} - {result.stderr}", 'backup')
            
            elif mount_type == 'bind':
                basename = os.path.basename(source)
                if not basename or basename == '/':
                    log_service.warning(f"跳过无效的绑定挂载: {source}", 'backup')
                    continue
                
                bind_tar = backup_dir / f"bind-{basename}.tar.gz"
                dirname = os.path.dirname(source)
                if not dirname or dirname == '/':
                    dirname = '/'
                
                host_source = Path("/host") / source.lstrip('/')
                if not host_source.exists():
                    log_service.warning(f"绑定挂载路径不存在: {host_source}", 'backup')
                    continue
                
                host_dirname = os.path.dirname(str(host_source))
                if not host_dirname or host_dirname == '/':
                    host_dirname = '/'
                
                result = subprocess.run(
                    ["tar", "-czvf", str(bind_tar), "-C", host_dirname, basename],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                if result.returncode == 0:
                    packed_volumes.append({
                        'name': basename,
                        'type': 'bind',
                        'source': source,
                        'destination': destination,
                        'file': str(bind_tar)
                    })
                else:
                    log_service.warning(f"打包绑定挂载失败: {source} - {result.stderr}", 'backup')
        
        return True, packed_volumes
    except Exception as e:
        return False, f"打包卷异常: {str(e)}"

def create_container_backup(container_id: str) -> Dict:
    """创建容器完整备份"""
    try:
        container_info = get_container_by_id(container_id)
        if not container_info:
            return {"success": False, "message": "容器不存在"}
        
        container_name = container_info.get('name', '')
        backup_name = f"{container_name}-backup"
        
        timestamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
        backup_dir = BACKUPS_DIR / f"{backup_name}-{timestamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        log_service.info(f"开始创建容器备份: {container_name}", 'backup')
        
        was_running = container_info.get('status') == 'running'
        
        if was_running:
            subprocess.run(["docker", "stop", container_id], capture_output=True)
            log_service.info(f"备份前停止容器: {container_name}", 'backup')
        
        steps = []
        
        success, result = save_image(container_id, backup_dir)
        if not success:
            shutil.rmtree(backup_dir, ignore_errors=True)
            if was_running:
                subprocess.run(["docker", "start", container_id], capture_output=True)
            return {"success": False, "message": result}
        steps.append("镜像保存成功")
        log_service.info(f"镜像保存成功: {container_name}", 'backup')
        
        success, result = export_config(container_id, backup_dir)
        if not success:
            shutil.rmtree(backup_dir, ignore_errors=True)
            if was_running:
                subprocess.run(["docker", "start", container_id], capture_output=True)
            return {"success": False, "message": result}
        steps.append("配置导出成功")
        log_service.info(f"配置导出成功: {container_name}", 'backup')
        
        success, volumes = pack_volumes(container_id, backup_dir)
        if not success:
            shutil.rmtree(backup_dir, ignore_errors=True)
            if was_running:
                subprocess.run(["docker", "start", container_id], capture_output=True)
            return {"success": False, "message": volumes}
        steps.append(f"卷打包成功 ({len(volumes)} 个)")
        log_service.info(f"卷打包成功: {container_name} ({len(volumes)} 个)", 'backup')
        
        archive_path = BACKUPS_DIR / f"{backup_name}-{timestamp}.tar"
        
        result = subprocess.run(
            ["tar", "-cf", str(archive_path), "-C", str(BACKUPS_DIR), f"{backup_name}-{timestamp}"],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode != 0:
            shutil.rmtree(backup_dir, ignore_errors=True)
            return {"success": False, "message": f"归档失败: {result.stderr}"}
        
        shutil.rmtree(backup_dir, ignore_errors=True)
        
        if was_running:
            subprocess.run(["docker", "start", container_id], capture_output=True)
            log_service.info(f"备份完成后重启容器: {container_name}", 'backup')
        
        backup_size = os.path.getsize(archive_path)
        
        backup_id = add_backup(
            container_id=container_id,
            container_name=container_name,
            name=backup_name,
            file_path=str(archive_path),
            size=backup_size,
            status='success'
        )
        
        log_service.success(f"容器备份创建成功: {container_name}", 'backup')
        
        return {
            "success": True,
            "message": "备份创建成功",
            "data": {
                "id": backup_id,
                "name": backup_name,
                "container_name": container_name,
                "container_id": container_id,
                "file_path": str(archive_path),
                "size": backup_size,
                "created_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
                "steps": steps
            }
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "备份操作超时"}
    except Exception as e:
        log_service.error(f"容器备份失败: {container_id} - {str(e)}", 'backup')
        return {"success": False, "message": f"备份失败: {str(e)}"}

def get_all_backups_list() -> list:
    """获取所有备份列表"""
    try:
        return get_all_backups()
    except Exception as e:
        log_service.error(f"获取备份列表失败: {str(e)}", 'backup')
        return []

def get_backups_for_container(container_name: str) -> list:
    """获取指定容器的备份列表"""
    try:
        return get_backups_by_container(container_name)
    except Exception as e:
        log_service.error(f"获取容器备份列表失败: {container_name} - {str(e)}", 'backup')
        return []

def get_backup_by_id(backup_id: int) -> dict:
    """获取单个备份详情"""
    try:
        return db_get_backup_by_id(backup_id)
    except Exception as e:
        log_service.error(f"获取备份详情失败: {backup_id} - {str(e)}", 'backup')
        return None

def remove_backup(backup_id: int) -> Dict:
    """删除备份"""
    try:
        success, file_path = delete_backup_by_id(backup_id)
        
        if success and file_path and os.path.exists(file_path):
            os.remove(file_path)
        
        if success:
            log_service.warning(f"备份已删除: ID={backup_id}", 'backup')
            return {"success": True, "message": "备份删除成功"}
        else:
            return {"success": False, "message": "备份不存在"}
    except Exception as e:
        log_service.error(f"删除备份失败: {backup_id} - {str(e)}", 'backup')
        return {"success": False, "message": f"删除失败: {str(e)}"}

def restore_backup(backup_id: int) -> Dict:
    """恢复备份"""
    try:
        backup = get_backup_by_id(backup_id)
        if not backup:
            return {"success": False, "message": "备份不存在"}
        
        archive_path = backup.get('file_path', '')
        if not archive_path or not os.path.exists(archive_path):
            return {"success": False, "message": "备份文件不存在"}
        
        container_name = backup.get('container_name', '')
        
        restore_dir = BACKUPS_DIR / f"restore-{container_name}-{datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')}"
        restore_dir.mkdir(parents=True, exist_ok=True)
        
        result = subprocess.run(
            ["tar", "-xf", archive_path, "-C", str(restore_dir)],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode != 0:
            shutil.rmtree(restore_dir, ignore_errors=True)
            return {"success": False, "message": f"解压备份失败: {result.stderr}"}
        
        image_path = restore_dir / f"{container_name}-backup" / "image.tar"
        config_path = restore_dir / f"{container_name}-backup" / "container-config.json"
        
        if image_path.exists():
            result = subprocess.run(
                ["docker", "load", "-i", str(image_path)],
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode != 0:
                shutil.rmtree(restore_dir, ignore_errors=True)
                return {"success": False, "message": f"加载镜像失败: {result.stderr}"}
            log_service.info(f"镜像加载成功: {container_name}", 'backup')
        
        shutil.rmtree(restore_dir, ignore_errors=True)
        
        log_service.success(f"容器备份恢复成功: {container_name}", 'backup')
        
        return {
            "success": True,
            "message": "备份恢复成功",
            "data": {
                "container_name": container_name
            }
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "恢复操作超时"}
    except Exception as e:
        log_service.error(f"恢复备份失败: {backup_id} - {str(e)}", 'backup')
        return {"success": False, "message": f"恢复失败: {str(e)}"}