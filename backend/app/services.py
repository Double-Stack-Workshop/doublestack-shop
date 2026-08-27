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

APP_ROOT = Path(__file__).resolve().parent.parent
REPOS_DIR = Path(os.getenv("REPOS_DIR", APP_ROOT / "repos")).resolve()
REPOS_DIR.mkdir(exist_ok=True)

DATA_DIR = Path(os.getenv("DATA_DIR", APP_ROOT / "data")).resolve()
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

    compose_command = get_compose_command()
    if not compose_command:
        yield {
            "type": "done",
            "success": False,
            "message": "未检测到可用的 Docker Compose（docker compose 或 docker-compose）",
            "data": {"repo_name": repo_name, "file_path": file_path, "status": "error"},
            "ts": _now_iso(),
            "elapsed_sec": 0,
        }
        return

    for repo in repos_db:
        if repo.name == repo_name:
            for yml_file in repo.yml_files:
                if yml_file.path == file_path or yml_file.name == file_path:
                    # 使用实际的仓库目录名称，而不是自定义名称
                    actual_repo_dir_name = repo.repo_dir_name if repo.repo_dir_name else get_repo_name_from_url(repo.url)
                    repo_dir = REPOS_DIR / actual_repo_dir_name
                    yml_full_path = (repo_dir / yml_file.path).resolve()
                    execution_compose_command = compose_command
                    execution_yml_path = yml_full_path
                    host_yml_path = get_host_mapped_path(yml_full_path)
                    host_compose_command = get_host_compose_command() if host_yml_path else []
                    if host_compose_command:
                        execution_compose_command = host_compose_command
                        execution_yml_path = host_yml_path

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
                    # 统一所有部署的 compose 项目名，使多个 yml 合并到同一个 project 下管理
                    env["COMPOSE_PROJECT_NAME"] = "doublestack-shop"

                    deployment_logs = []
                    deploy_started_at = time.time()

                    def log(msg, stage="start"):
                        deployment_logs.append(msg)
                        return {"type": "log", "level": "info", "stage": stage, "message": msg, "ts": _now_iso()}

                    try:
                        if not yml_full_path.is_file():
                            yield {
                                "type": "done",
                                "success": False,
                                "message": f"部署文件不存在: {yml_full_path}",
                                "data": {"repo_name": repo_name, "file_path": yml_file.path, "status": "error"},
                                "ts": _now_iso(),
                                "elapsed_sec": 0,
                            }
                            return
                        yield log(f"[部署开始] 正在处理文件: {yml_file.name}")
                        yield log(f"[部署开始] 文件路径: {execution_yml_path}")

                        # 拉取镜像（实时流式输出）
                        pull_returncode = yield from _stream_command(
                            execution_compose_command + ["-f", str(execution_yml_path), "pull"],
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
                            execution_compose_command + ["-f", str(execution_yml_path), "up", "-d"],
                            env, timeout=300, stage="up", label="[启动日志]",
                            deployment_logs=deployment_logs,
                        )

                        if up_returncode == 0:
                            container_id = None
                            container_name = None

                            try:
                                services_result = subprocess.run(
                                    execution_compose_command + ["-f", str(execution_yml_path), "config", "--services"],
                                    capture_output=True,
                                    text=True,
                                    timeout=30,
                                    env=env,
                                )
                                services = services_result.stdout.splitlines() if services_result.returncode == 0 else []
                                for service in services:
                                    ps_result = subprocess.run(
                                        execution_compose_command + ["-f", str(execution_yml_path), "ps", "-q", service],
                                        capture_output=True,
                                        text=True,
                                        timeout=30,
                                        env=env,
                                    )
                                    if ps_result.returncode == 0 and ps_result.stdout.strip():
                                        container_id = ps_result.stdout.strip().splitlines()[0]
                                        break
                                if container_id:
                                    inspect_result = subprocess.run(
                                        ["docker", "inspect", "--format", "{{.Name}}", container_id],
                                        capture_output=True,
                                        text=True,
                                        timeout=30,
                                    )
                                    if inspect_result.returncode == 0:
                                        container_name = inspect_result.stdout.strip().lstrip("/")
                            except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
                                log_service.warning(f"部署后无法读取容器信息: {exc}", 'deploy')

                            add_deployment(repo_name, yml_file.name, container_id, container_name, 'deployed', '部署成功')

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
                        log_service.error(f"Docker Compose 命令未找到: {yml_file.name}", 'deploy')
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

def test_connectivity(url: str, timeout: int = 10) -> Dict:
    """测试网络连通性。
    用户指定使用 curl 命令参数：
        curl --connect-timeout 8 -s -o /dev/null <URL>
    我们在此基础上追加 -w 拿到 HTTP 状态码和总耗时，便于前端展示 latency。
    为保证结果等于「在宿主机终端手动执行」，通过 _run_on_host 穿透执行，
    这样不会被容器内的代理/命名空间/路由干扰。
    """
    import shlex
    # 用户给的 connect-timeout 固定为 8 秒，外层总超时宽松一点
    connect_timeout_sec = 8
    total_timeout = max(timeout, connect_timeout_sec + 3)

    # -w：输出一行 "HTTP_CODE TIME_TOTAL_SECONDS"，例如 "200 0.089314"
    safe_url = shlex.quote(url)
    curl_cmd = (
        f"curl --connect-timeout {connect_timeout_sec} -s -o /dev/null "
        f"-w '%{{http_code}} %{{time_total}}' {safe_url}"
    )
    try:
        rc, stdout, stderr, source = _run_on_host(curl_cmd, timeout=total_timeout)
        http_code = 0
        time_total_s = 0.0
        out = (stdout or "").strip().split()
        if len(out) >= 1 and out[0].isdigit():
            http_code = int(out[0])
        if len(out) >= 2:
            try:
                time_total_s = float(out[1])
            except (ValueError, TypeError):
                time_total_s = 0.0

        latency = int(round(time_total_s * 1000)) if time_total_s > 0 else 0

        # curl exit 0 + 有 HTTP 响应 → 网络连通（含 401/403 这种鉴权失败，不算网络不通）
        network_ok = (rc == 0 and http_code > 0)

        if network_ok and http_code < 400:
            return {
                "success": True,
                "url": url,
                "status": "reachable",
                "latency": latency,
                "status_code": http_code,
                "message": "连接成功",
                "_exec_source": source,
            }
        if network_ok and http_code >= 400:
            # 401/403/404：HTTP层能返回，网络本身通（registry-1.docker.io/v2 默认401）
            return {
                "success": True,
                "url": url,
                "status": "reachable",
                "latency": latency,
                "status_code": http_code,
                "message": "网络可达",
                "_exec_source": source,
            }
        if rc == 28:
            # curl: (28) 连接超时
            return {
                "success": False,
                "url": url,
                "status": "timeout",
                "latency": connect_timeout_sec * 1000,
                "status_code": 0,
                "message": "连接超时（curl exit 28）",
                "_exec_source": source,
                "_stderr": (stderr or "")[:500],
            }
        if rc == 60:
            return {
                "success": False,
                "url": url,
                "status": "ssl_error",
                "latency": latency,
                "status_code": 0,
                "message": "SSL证书错误（curl exit 60）",
                "_exec_source": source,
                "_stderr": (stderr or "")[:500],
            }
        if rc == 6:
            return {
                "success": False,
                "url": url,
                "status": "connection_error",
                "latency": 0,
                "status_code": 0,
                "message": "DNS 解析失败（curl exit 6）",
                "_exec_source": source,
                "_stderr": (stderr or "")[:500],
            }
        if rc == 7:
            return {
                "success": False,
                "url": url,
                "status": "connection_error",
                "latency": 0,
                "status_code": 0,
                "message": "无法连接到主机（curl exit 7）",
                "_exec_source": source,
                "_stderr": (stderr or "")[:500],
            }
        return {
            "success": False,
            "url": url,
            "status": "unreachable",
            "latency": latency,
            "status_code": http_code,
            "message": f"连通性失败（curl exit {rc}, HTTP {http_code}）",
            "_exec_source": source,
            "_stderr": (stderr or "")[:500],
        }
    except Exception as e1:
        # 极端场景：宿主机没装 curl → 用 Python requests 兜底测试容器网络
        try:
            proxies = get_requests_proxies()
            start_time = time.time()
            response = requests.get(url, timeout=timeout, verify=True, proxies=proxies)
            latency = int((time.time() - start_time) * 1000)
            if 200 <= response.status_code < 400:
                return {
                    "success": True,
                    "url": url,
                    "status": "reachable",
                    "latency": latency,
                    "status_code": response.status_code,
                    "message": "连接成功（容器网络 requests 兜底）",
                    "_exec_source": "requests-fallback",
                }
            return {
                "success": False,
                "url": url,
                "status": "unreachable",
                "latency": latency,
                "status_code": response.status_code,
                "message": f"连接失败，HTTP状态码: {response.status_code}",
                "_exec_source": "requests-fallback",
            }
        except requests.exceptions.Timeout:
            return {
                "success": False,
                "url": url,
                "status": "timeout",
                "latency": timeout * 1000,
                "status_code": 0,
                "message": "连接超时（requests 兜底）",
                "_exec_source": "requests-fallback",
            }
        except requests.exceptions.SSLError:
            return {
                "success": False,
                "url": url,
                "status": "ssl_error",
                "latency": 0,
                "status_code": 0,
                "message": "SSL证书错误（requests 兜底）",
                "_exec_source": "requests-fallback",
            }
        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "url": url,
                "status": "connection_error",
                "latency": 0,
                "status_code": 0,
                "message": "连接失败，无法建立连接（requests 兜底）",
                "_exec_source": "requests-fallback",
            }
        except Exception as e2:
            return {
                "success": False,
                "url": url,
                "status": "error",
                "latency": 0,
                "status_code": 0,
                "message": f"测试异常: {str(e1)} / fallback: {str(e2)}",
                "_exec_source": "error",
            }


def test_all_connectivity() -> Dict:
    """测试所有预设的网络连接——严格对应用户在终端里手工执行的命令：
        echo -n "GitHub: ";curl --connect-timeout 8 -s -o /dev/null https://github.com && echo "OK" || echo "FAIL"
        echo -n "Docker Registry: ";curl --connect-timeout 8 -s -o /dev/null https://registry-1.docker.io/v2/ && echo "OK" || echo "FAIL"
    """
    targets = [
        {"name": "GitHub",           "url": "https://github.com"},
        {"name": "Docker Registry",  "url": "https://registry-1.docker.io/v2/"},
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
        except (TypeError, ValueError):
            return False
    
    if http_proxy and not validate_proxy(http_proxy):
        log_service.error("代理配置失败: HTTP代理格式不正确", 'system')
        return {"success": False, "message": "HTTP代理格式不正确，请使用 http://ip:port 格式"}
    
    if https_proxy and not validate_proxy(https_proxy):
        log_service.error("代理配置失败: HTTPS代理格式不正确", 'system')
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

def detect_docker_compose() -> dict:
    """分别检测 docker-compose(v1) 和 docker compose(v2)，返回版本与升级状态。

    注意：所有检测均通过 _run_on_host 在「宿主机用户空间」执行，确保拿到的是
    用户宿主机上真实安装的 Docker Compose 版本，而不是容器镜像里自带的 Debian 包版本。
    """
    v1_version = ""
    v2_version = ""
    v1_found = False
    v2_found = False
    detection_source = "none"

    # 检测 v2: docker compose (空格分隔，Go 插件版本) — 先测 v2，因为是主流
    try:
        rc, stdout, stderr, src = _run_on_host("docker compose version", timeout=20)
        detection_source = src
        if rc == 0:
            raw = (stdout or stderr or "").strip()
            if raw and "not a docker command" not in raw.lower() and "unknown" not in raw.lower():
                v2_version = raw
                v2_found = True
    except Exception:
        pass

    # 检测 v1: docker-compose (横杠分隔，Python 版本)
    try:
        rc, stdout, stderr, src = _run_on_host("docker-compose --version", timeout=15)
        if not v2_found or detection_source == "none":
            detection_source = src
        if rc == 0:
            raw = (stdout or stderr or "").strip()
            if raw:
                v1_version = raw
                v1_found = True
    except Exception:
        pass

    # 判断升级需求：只要没有 v2，就认为需要升级/安装
    if v2_found:
        status = "v2_installed"
        needs_upgrade = False
    elif v1_found:
        status = "v1_only"
        needs_upgrade = True
    else:
        status = "not_installed"
        needs_upgrade = True

    return {
        "v1_version": v1_version,
        "v2_version": v2_version,
        "v1_found": v1_found,
        "v2_found": v2_found,
        "status": status,            # v2_installed / v1_only / not_installed
        "needs_upgrade": needs_upgrade,
        "detection_source": detection_source,  # 标识最终在哪层执行拿到的结果
    }


def get_compose_command() -> list:
    """返回可读取容器内 Compose 文件的命令，优先 v2。"""
    # Compose CLI 必须能读取 /app/repos 中的配置文件，因此不在宿主机 chroot 内执行。
    candidates = (["docker", "compose"], ["docker-compose"])

    for command in candidates:
        try:
            result = subprocess.run(
                command + ["version"], capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                return command
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return []


def get_host_compose_command() -> list:
    """返回在宿主机根目录中执行的 Compose 命令，用于读取宿主机映射文件。"""
    if not (Path("/host/usr/bin/docker").exists() or Path("/host/bin/docker").exists()):
        return []
    for command in (["chroot", "/host", "docker", "compose"], ["chroot", "/host", "docker-compose"]):
        try:
            result = subprocess.run(command + ["version"], capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                return command
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return []


def generate_compose_upgrade_script() -> str:
    """生成 Docker Compose v1→v2 升级脚本到 scripts 目录，返回脚本路径。
    脚本设计在宿主机上用 bash 执行（通过 /host 挂载路径也可从容器内触发到宿主机）。
    """
    import os
    # 与 generate_update_script 保持一致的目录
    script_dir = "/app/scripts"
    script_path = os.path.join(script_dir, "upgrade_docker_compose_to_v2.sh")
    os.makedirs(script_dir, exist_ok=True)

    script_content = r"""#!/bin/bash
# Docker Compose v1 → v2 升级脚本
# 适用系统：Ubuntu / Debian / CentOS / RHEL / Fedora (x86_64 / aarch64)
#
# - 若检测到 Python 版 docker-compose(v1)：卸载 pip/docker-compose 二进制，清理别名
# - 若完全未安装：直接安装 docker-compose-plugin(v2)
# - 升级完成后验证：docker compose version

set -e

echo "=============================================="
echo "  Docker Compose v1  →  v2  升级脚本"
echo "=============================================="
echo ""

# ========== 0. 权限检测 ==========
if [ "$(id -u)" -ne 0 ]; then
    echo "[警告] 当前用户非 root，部分步骤可能需要 sudo。"
    echo "       如需完全自动升级，请用: sudo bash $0"
    echo ""
fi

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
fi

# ========== 1. 系统检测 ==========
echo "[1/5] 检测系统环境..."

ARCH=$(uname -m)
case "$ARCH" in
    x86_64|amd64)  ARCH_ALT="x86_64" ;;
    aarch64|arm64) ARCH_ALT="aarch64" ;;
    armv7l)        ARCH_ALT="armv7" ;;
    *)
        echo "不支持的架构: $ARCH"
        exit 1
        ;;
esac
echo "  架构: $ARCH ($ARCH_ALT)"

PKG_MGR=""
DISTRO=""
if command -v apt-get >/dev/null 2>&1; then
    PKG_MGR="apt"
    DISTRO=$(. /etc/os-release 2>/dev/null && echo "$ID" || echo "debian")
    echo "  发行版: Debian/Ubuntu 系 ($DISTRO)，包管理器: apt"
elif command -v dnf >/dev/null 2>&1; then
    PKG_MGR="dnf"
    DISTRO=$(. /etc/os-release 2>/dev/null && echo "$ID" || echo "fedora")
    echo "  发行版: RHEL/Fedora 系 ($DISTRO)，包管理器: dnf"
elif command -v yum >/dev/null 2>&1; then
    PKG_MGR="yum"
    DISTRO=$(. /etc/os-release 2>/dev/null && echo "$ID" || echo "centos")
    echo "  发行版: CentOS/RHEL 系 ($DISTRO)，包管理器: yum"
else
    PKG_MGR="manual"
    echo "  未识别包管理器，将使用二进制直装方案"
fi

echo ""

# ========== 2. 检测并卸载旧版 v1 ==========
echo "[2/5] 检测并清理旧版 Docker Compose v1 (docker-compose)..."

V1_UNINSTALLED=0

# 2.1 pip 安装
if command -v pip3 >/dev/null 2>&1; then
    if pip3 show docker-compose >/dev/null 2>&1; then
        echo "  → 发现 pip 安装的 docker-compose，正在卸载..."
        $SUDO pip3 uninstall -y docker-compose || true
        V1_UNINSTALLED=1
    fi
fi
if command -v pip >/dev/null 2>&1; then
    if pip show docker-compose >/dev/null 2>&1; then
        echo "  → 发现 pip2 安装的 docker-compose，正在卸载..."
        $SUDO pip uninstall -y docker-compose || true
        V1_UNINSTALLED=1
    fi
fi

# 2.2 /usr/local/bin/docker-compose 二进制
if [ -f /usr/local/bin/docker-compose ]; then
    echo "  → 发现 /usr/local/bin/docker-compose 二进制，正在删除..."
    $SUDO rm -f /usr/local/bin/docker-compose
    V1_UNINSTALLED=1
fi
if [ -f /usr/bin/docker-compose ]; then
    echo "  → 发现 /usr/bin/docker-compose 二进制，正在删除..."
    $SUDO rm -f /usr/bin/docker-compose
    V1_UNINSTALLED=1
fi

# 2.3 ~/.docker/cli-plugins 下残留旧插件（v1插件极少，兜底）
if [ -f ~/.docker/cli-plugins/docker-compose ]; then
    echo "  → 清理用户级 cli-plugins 下的旧 compose 文件"
    rm -f ~/.docker/cli-plugins/docker-compose || true
fi

[ $V1_UNINSTALLED -eq 1 ] && echo "  旧版 v1 已清理完成。" || echo "  未检测到 v1，跳过清理。"
echo ""

# ========== 3. 安装 v2 (docker-compose-plugin) ==========
echo "[3/5] 安装 Docker Compose v2 (docker compose plugin)..."

INSTALLED_V2=0

# 3.1 apt 系
if [ "$PKG_MGR" = "apt" ]; then
    echo "  → 尝试用 apt 安装 docker-compose-plugin..."
    set +e
    $SUDO apt-get update -y -qq
    $SUDO apt-get install -y -qq docker-compose-plugin
    RC=$?
    set -e
    if [ $RC -eq 0 ]; then
        INSTALLED_V2=1
    else
        echo "  apt 安装失败，回退到二进制直装..."
    fi
fi

# 3.2 dnf / yum 系
if [ $INSTALLED_V2 -eq 0 ] && { [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; }; then
    echo "  → 尝试用 $PKG_MGR 安装 docker-compose-plugin..."
    set +e
    $SUDO $PKG_MGR install -y docker-compose-plugin
    RC=$?
    set -e
    if [ $RC -eq 0 ]; then
        INSTALLED_V2=1
    else
        echo "  $PKG_MGR 安装失败，回退到二进制直装..."
    fi
fi

# 3.3 二进制直装兜底（手动 / GitHub Release）
if [ $INSTALLED_V2 -eq 0 ]; then
    echo "  → 下载官方二进制到 Docker CLI plugins 目录..."
    PLUGIN_DIR="/usr/local/lib/docker/cli-plugins"
    $SUDO mkdir -p "$PLUGIN_DIR"
    BIN_URL="https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${ARCH_ALT}"
    echo "    下载地址: $BIN_URL"
    set +e
    if command -v curl >/dev/null 2>&1; then
        $SUDO curl -SL "$BIN_URL" -o "$PLUGIN_DIR/docker-compose"
    elif command -v wget >/dev/null 2>&1; then
        $SUDO wget -q -O "$PLUGIN_DIR/docker-compose" "$BIN_URL"
    else
        echo "  curl 和 wget 都不可用，请先安装 curl 或 wget，然后手动下载："
        echo "    $BIN_URL"
        echo "  放到: $PLUGIN_DIR/docker-compose 并 chmod +x"
        exit 1
    fi
    RC=$?
    set -e
    if [ $RC -ne 0 ]; then
        echo "  二进制下载失败，请检查网络或代理设置。"
        exit 1
    fi
    $SUDO chmod +x "$PLUGIN_DIR/docker-compose"

    # 兼容：在 /usr/local/bin 放一个 docker-compose 别名脚本，让老命令仍可用
    if [ ! -e /usr/local/bin/docker-compose ]; then
        echo "  → 创建兼容别名 /usr/local/bin/docker-compose → docker compose"
        $SUDO tee /usr/local/bin/docker-compose >/dev/null <<'ALIAS_EOF'
#!/bin/sh
exec docker compose "$@"
ALIAS_EOF
        $SUDO chmod +x /usr/local/bin/docker-compose
    fi

    INSTALLED_V2=1
fi

echo "  v2 安装完成。"
echo ""

# ========== 4. 验证安装 ==========
echo "[4/5] 验证安装结果..."

if command -v docker >/dev/null 2>&1; then
    echo ""
    echo "  Docker Compose v2 版本:"
    docker compose version
    echo ""
    if command -v docker-compose >/dev/null 2>&1; then
        echo "  兼容命令 docker-compose 测试:"
        docker-compose --version
    fi
else
    echo "  [警告] 当前环境未检测到 docker 命令（可能在容器内执行），请在宿主机验证："
    echo "    docker compose version"
fi

echo ""

# ========== 5. 提示信息 ==========
echo "[5/5] 升级完成"
echo ""
echo "=============================================="
echo "  使用方式："
echo "    旧命令 (v1): docker-compose up -d"
echo "    新命令 (v2): docker compose up -d      ← 推荐"
echo ""
echo "  为兼容已有脚本，已保留 docker-compose 别名。"
echo "=============================================="
"""

    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script_content)
    os.chmod(script_path, 0o755)
    return script_path


def get_docker_info():
    docker_version = ""
    compose_info = {}

    # 优先读 Server 端真实版本（通过 docker.sock 直接问宿主机 dockerd）
    # 因为容器内的 `docker --version` 只会显示容器镜像里自带的客户端版本，
    # 与用户宿主机实际安装的 Docker Engine 可能差异很大（例如 Debian 包 26 vs 宿主机 28）
    try:
        server_fmt = (
            "docker version --format "
            "'Docker version {{.Server.Version}}, build {{.Server.GitCommit}}'"
        )
        result = subprocess.run(["sh", "-c", server_fmt], capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            docker_version = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Server 端拿不到，就退到 Client 端（但这通常是容器内打包的客户端版本，不准但比空好）
    if not docker_version:
        try:
            client_fmt = (
                "docker version --format "
                "'Docker version {{.Client.Version}}, build {{.Client.GitCommit}}'"
            )
            result = subprocess.run(["sh", "-c", client_fmt], capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout.strip():
                docker_version = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    # 最后兜底：旧的 --version
    if not docker_version:
        try:
            result = subprocess.run(["docker", "--version"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                docker_version = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    if not docker_version:
        docker_version = "Docker 未安装或不可用"

    compose_info = detect_docker_compose()

    # 若需要升级，自动生成脚本（参考 check-update 生成应用升级脚本的行为）
    if compose_info.get("needs_upgrade"):
        try:
            script_path = generate_compose_upgrade_script()
            compose_info["upgrade_script_generated"] = True
            compose_info["upgrade_script_path"] = script_path
            # 生成脚本后立即反向推导宿主机路径 + 可执行命令
            host_resolved = resolve_host_scripts_dir("upgrade_docker_compose_to_v2.sh")
            compose_info["host"] = host_resolved
            compose_info["run_command_one_liner"] = host_resolved.get("command_one_liner")
            compose_info["run_command_cd_style"] = host_resolved.get("command_cd_style")
        except Exception as e:
            compose_info["upgrade_script_generated"] = False
            compose_info["upgrade_script_error"] = str(e)
    else:
        compose_info["upgrade_script_generated"] = False
        # 即使不需要升级，也提供路径推导信息（方便将来其他脚本复用）
        try:
            host_resolved = resolve_host_scripts_dir("upgrade_docker_compose_to_v2.sh")
            compose_info["host"] = host_resolved
        except Exception:
            pass

    # 兼容旧字段，同时返回详细字段
    docker_compose_version = compose_info.get("v2_version") or compose_info.get("v1_version") or "Docker Compose 未安装或不可用"

    return {
        "docker_version": docker_version,
        "docker_compose_version": docker_compose_version,  # 向后兼容字段
        "compose": compose_info,                           # 新字段：v1/v2/status/needs_upgrade/脚本路径等
    }


def get_current_container_id() -> str:
    """获取当前运行进程所在的 Docker 容器 ID；非容器环境返回空字符串。
    优先从 cgroup 提取（兼容 v1/v2），回退到 /etc/hostname 短 ID。
    """
    import os, re

    cgroup_paths = ["/proc/self/cgroup", "/proc/1/cgroup"]
    for cg in cgroup_paths:
        try:
            if not os.path.exists(cg):
                continue
            with open(cg, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            # cgroup v1: 1:cpu:/docker/<64位ID> 或 11:devices:/system.slice/docker-<ID>.scope
            # cgroup v2: 0::/system.slice/docker-<ID>.scope 或 0::/docker/<ID>
            m64 = re.search(r"[0-9a-f]{64}", content)
            if m64:
                return m64.group(0)
            m12_scope = re.search(r"docker-([0-9a-f]{12})\.scope", content)
            if m12_scope:
                return m12_scope.group(1)
        except Exception:
            continue

    # 回退：hostname 默认就是容器 ID 的前 12 位
    hn = ""
    try:
        hn = os.uname()[1]
    except Exception:
        try:
            with open("/etc/hostname", "r", encoding="utf-8", errors="ignore") as f:
                hn = f.read().strip()
        except Exception:
            hn = ""
    if hn and re.fullmatch(r"[0-9a-f]{12}", hn):
        return hn
    return ""


def get_host_mapped_path(container_path: Path) -> Optional[Path]:
    """将容器内路径反推为当前应用容器 bind mount 对应的宿主机绝对路径。"""
    container_id = get_current_container_id()
    if not container_id:
        return None
    try:
        result = subprocess.run(
            ["docker", "inspect", container_id], capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        mounts = json.loads(result.stdout)[0].get("Mounts", [])
        path = container_path.resolve()
        candidates = []
        for mount in mounts:
            if mount.get("Type") != "bind" or not mount.get("Source") or not mount.get("Destination"):
                continue
            destination = Path(mount["Destination"])
            try:
                relative = path.relative_to(destination)
            except ValueError:
                continue
            candidates.append((len(destination.parts), Path(mount["Source"]) / relative))
        return max(candidates, key=lambda item: item[0])[1] if candidates else None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, IndexError, json.JSONDecodeError):
        return None


def wait_for_docker_ready(timeout_seconds: int = 45, interval_seconds: int = 2) -> bool:
    """等待 Docker daemon 恢复；仅检查 docker.sock 可用性，不修改宿主机状态。"""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True, text=True, timeout=8,
            )
            if result.returncode == 0 and result.stdout.strip():
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        time.sleep(interval_seconds)
    return False


def schedule_host_docker_restart(delay_seconds: int = 3) -> Dict:
    """安排重启宿主机 Docker 服务。

    应用本身运行在容器中，直接调用 ``systemctl`` 只能影响容器命名空间。
    因此复用当前应用镜像启动一个短生命周期的特权辅助容器，并让它加入
    宿主机 PID/挂载/网络命名空间后执行 systemctl。延迟数秒可确保 HTTP
    响应先返回给浏览器，避免 Docker 重启导致请求在半途中断开。
    """
    container_id = get_current_container_id()
    if not container_id:
        return {
            "success": False,
            "message": "未检测到当前应用容器，无法自动重启宿主机 Docker"
        }

    try:
        inspect_result = subprocess.run(
            ["docker", "inspect", container_id],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if inspect_result.returncode != 0:
            error = (inspect_result.stderr or inspect_result.stdout or "未知错误").strip()
            return {"success": False, "message": f"无法读取当前容器信息: {error}"}

        info = json.loads(inspect_result.stdout)[0]
        image = (info.get("Config") or {}).get("Image")
        if not image:
            return {"success": False, "message": "无法确定当前应用镜像，无法创建重启任务"}

        helper_name = f"doublestack-docker-restart-{int(time.time())}"
        restart_command = (
            f"sleep {max(1, delay_seconds)}; "
            "nsenter -t 1 -m -u -i -n -p -- sh -c "
            "'systemctl restart docker || service docker restart || /etc/init.d/docker restart'"
        )
        result = subprocess.run(
            [
                "docker", "run", "-d", "--rm",
                "--name", helper_name,
                "--privileged",
                "--pid=host",
                "--network=host",
                image,
                "sh", "-c", restart_command,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "未知错误").strip()
            return {"success": False, "message": f"创建 Docker 重启任务失败: {error}"}

        helper_id = result.stdout.strip()
        log_service.warning(
            "已安排重启宿主机 Docker 服务，运行中的容器会短暂中断", "system"
        )
        return {
            "success": True,
            "message": "Docker 重启任务已创建，服务将短暂不可用后自动恢复",
            "helper_container_id": helper_id,
        }
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"success": False, "message": f"创建 Docker 重启任务失败: {str(e)}"}
    except (IndexError, json.JSONDecodeError, OSError, subprocess.SubprocessError) as e:
        return {"success": False, "message": f"创建 Docker 重启任务失败: {str(e)}"}


def resolve_host_scripts_dir(script_filename: str = "upgrade_docker_compose_to_v2.sh") -> dict:
    """通过反向推导当前容器的挂载信息，得到脚本文件在**宿主机**上的绝对路径，
    并给出可直接在宿主机 shell 中粘贴执行的命令（一行式 + cd 式）。

    返回字段:
      resolved: bool            — 是否成功通过容器挂载反向推导出真实宿主机路径
      in_container: bool        — 当前进程是否运行在容器内
      container_id: str         — 当前容器 ID（空表示未识别出容器环境）
      scripts_dir_container: str — 脚本目录在容器内的路径 ("/app/scripts")
      scripts_dir_host: str     — 脚本目录在宿主机上的绝对路径（推导结果或fallback相对路径）
      script_file_host: str     — 脚本文件在宿主机上的绝对路径
      command_one_liner: str    — 一行式直接执行，例：sudo bash /xxx/backend/scripts/xxx.sh
      command_cd_style: str     — cd+执行两段式，例：cd /xxx/backend/scripts && sudo bash xxx.sh
    """
    import os
    CONTAINER_SCRIPTS_DIR = "/app/scripts"
    container_id = get_current_container_id()
    in_container = bool(container_id) or os.path.exists(CONTAINER_SCRIPTS_DIR)

    scripts_dir_host = None
    resolved = False

    if container_id:
        try:
            result = subprocess.run(
                ["docker", "inspect", container_id],
                capture_output=True,
                text=True,
                timeout=15
            )
            if result.returncode == 0:
                import json
                data = json.loads(result.stdout or "[]")
                if isinstance(data, list) and data:
                    mounts = data[0].get("Mounts") or []
                    for m in mounts:
                        dest = m.get("Destination") or ""
                        src = m.get("Source") or ""
                        if dest and dest.rstrip("/") == CONTAINER_SCRIPTS_DIR and src:
                            # 注意：inspect 返回的 Source 是 docker daemon 视角的宿主机绝对路径
                            scripts_dir_host = src
                            resolved = True
                            break
                    # 兜底：如果 mount 中没精确匹配 /app/scripts，找包含 scripts/ 且带 Destination 的 bind mount
                    if not scripts_dir_host:
                        for m in mounts:
                            dest = (m.get("Destination") or "").rstrip("/")
                            src = m.get("Source") or ""
                            if (
                                dest.endswith("/scripts")
                                and (dest == CONTAINER_SCRIPTS_DIR or dest.endswith("/app/scripts"))
                                and src
                            ):
                                scripts_dir_host = src
                                resolved = True
                                break
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    # Fallback：推导失败时给出相对路径提示（用户通常在项目根目录执行）
    if not scripts_dir_host:
        scripts_dir_host = "./backend/scripts"

    # Windows 宿主机下 Source 可能是 /host_mnt/... 或者 linux 风格路径；保持原样即可
    script_file_host = os.path.join(scripts_dir_host, script_filename).replace("\\", "/")

    # 生成执行命令
    SUDO = "sudo "  # 大多数 Linux 宿主机需要
    command_one_liner = f"{SUDO}bash {script_file_host}"
    command_cd_style = f"cd {scripts_dir_host} && {SUDO}bash {script_filename}"

    return {
        "resolved": resolved,
        "in_container": in_container,
        "container_id": container_id,
        "scripts_dir_container": CONTAINER_SCRIPTS_DIR,
        "scripts_dir_host": scripts_dir_host,
        "script_file_host": script_file_host,
        "command_one_liner": command_one_liner,
        "command_cd_style": command_cd_style,
    }


def _parse_cpu_stat(text: str):
    """解析 /proc/stat 的第一行 cpu 总览，返回 (total_jiffies, idle_jiffies)。
    例：cpu  1234 2345 3456 45678 567 678 789 0 0 0
                     usr nice sys  idle  iowq irq soft steal ...
    """
    if not text:
        return None, None
    for line in text.splitlines():
        if line.startswith('cpu '):
            parts = line.split()
            if len(parts) < 5:
                return None, None
            try:
                nums = [int(x) for x in parts[1:]]
            except (ValueError, TypeError):
                return None, None
            # idle = parts[4] (idle) + parts[5] (iowait)
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
            total = sum(nums)
            return total, idle
    return None, None


def _read_host_text(path: str, max_bytes: int = 1024 * 1024) -> str:
    """优先读 /host/<path>（宿主机真实文件），不存在再读容器内 /<path>。
    用纯 Python open()，零 subprocess 开销。

    典型场景：
      /host/proc/stat       → 宿主机 CPU 累计 jiffies
      /host/proc/meminfo    → 宿主机内存总量/可用量
      /host/etc/os-release  → 宿主机发行版
    """
    import os
    host_path = "/host" + path
    for p in (host_path, path):
        try:
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(max_bytes)
                if content is not None:
                    return content
        except Exception:
            continue
    return ""


def _measure_cpu_usage(interval_sec: float = 1.0):
    """用 /proc/stat 两次采样差值计算真实瞬时 CPU 使用率。
    优先读 /host/proc/stat（宿主机真实 CPU），纯 Python open()，无 subprocess。
    """
    import time
    try:
        text1 = _read_host_text("/proc/stat")
        total1, idle1 = _parse_cpu_stat(text1)
        if total1 is None:
            return None

        # 间隔（由调用方控制长短：同步首屏用 0.2s 快出值，SSE 实时推用 1s 更准）
        time.sleep(interval_sec)

        text2 = _read_host_text("/proc/stat")
        total2, idle2 = _parse_cpu_stat(text2)
        if total2 is None:
            return None

        delta_total = total2 - total1
        delta_idle = idle2 - idle1
        if delta_total <= 0:
            return 0.0
        usage = (delta_total - delta_idle) / delta_total * 100.0
        if usage < 0:
            usage = 0.0
        if usage > 100:
            usage = 100.0
        return round(usage, 1)
    except Exception:
        return None


def _measure_memory_usage():
    """快速采集内存占用，优先读 /host/proc/meminfo（宿主机真实内存），
    纯 Python 文件 I/O，无 subprocess。返回 dict 或 None。
    """
    try:
        text = _read_host_text("/proc/meminfo")
        if not text:
            return None
        mem_total = 0
        mem_available = 0
        for line in text.splitlines():
            if line.startswith('MemTotal:'):
                try:
                    mem_total = int(line.split()[1]) // 1024
                except (ValueError, IndexError):
                    pass
            elif line.startswith('MemAvailable:'):
                try:
                    mem_available = int(line.split()[1]) // 1024
                except (ValueError, IndexError):
                    pass
        if mem_total <= 0:
            return None
        mem_used = mem_total - mem_available
        usage = (mem_used / mem_total) * 100.0
        return {
            "memory_total": f"{mem_total} MB",
            "memory_used": f"{mem_used} MB",
            "memory_usage": f"{usage:.1f}%",
            "_mem_total_mb": mem_total,
            "_mem_avail_mb": mem_available,
        }
    except Exception:
        return None


def stream_host_metrics(cpu_interval_sec: float = 1.0, max_updates: int = 0):
    """SSE 实时指标生成器：逐条 yield 事件 dict。
    只推送会「实时变化」的指标（CPU + 内存），其他不常变信息（磁盘、OS版本、网卡、
    网络信息等）仍由 GET /api/system/host-info 一次性返回，避免每秒做昂贵的网络穿透。

    事件结构：{"type": "metrics", "cpu_usage": "12.3%", "memory_total/used/usage": "...", "ts": "ISO8601"}
    最后会发 {"type": "done"}

    max_updates == 0 表示不限条数（等客户端自己断开 SSE 连接）。
    """
    import time
    sent = 0
    try:
        while True:
            # CPU：两次采样差值（内置 1s 间隔）
            cpu_pct = _measure_cpu_usage(interval_sec=cpu_interval_sec)

            # 内存：/proc/meminfo 快读
            mem = _measure_memory_usage()

            payload = {
                "type": "metrics",
                "cpu_usage": f"{cpu_pct:.1f}%" if cpu_pct is not None else "无法获取",
                "memory_total": mem["memory_total"] if mem else "无法获取",
                "memory_used": mem["memory_used"] if mem else "无法获取",
                "memory_usage": mem["memory_usage"] if mem else "无法获取",
                "ts": _now_iso(),
            }
            yield payload
            sent += 1

            # 达到上限退出（用于调试）
            if max_updates > 0 and sent >= max_updates:
                yield {"type": "done", "success": True, "sent": sent}
                return

            # CPU 采样已经带 sleep(cpu_interval_sec) 了，这里再补一个 50ms 避免极端情况下紧循环
            time.sleep(0.05)
    except (GeneratorExit, OSError, RuntimeError):
        # 客户端断开 SSE 连接时 FastAPI 会关闭生成器，这里静默退出
        return


def _run_on_host(cmd_str: str, timeout: int = 20) -> tuple:
    """尽量在「宿主机用户空间」执行给定 shell 命令（字符串形式，可带管道/管道重定向），
    返回 (returncode, stdout, stderr, source)。

    适用场景：docker compose version / docker-compose --version / which xxx 等
    依赖「宿主机上实际安装的可执行文件 + PATH + 配置」的命令。

    三层 fallback（穿透能力从强到弱）：
    1. chroot /host sh -c '<cmd>'        — 直接把根切到宿主机，二进制/路径全是宿主机的
    2. nsenter mount+pid netns            — 进入宿主机的 mount 和 pid 命名空间
    3. 容器内本地执行（兜底）             — 拿到的是容器内版本，但至少有返回值
    """
    import os

    # 1. chroot /host
    if os.path.isdir("/host") and os.path.isfile("/host/etc/os-release"):
        try:
            full = ["chroot", "/host", "sh", "-c", cmd_str]
            r = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0 and (r.stdout or r.stderr):
                return (r.returncode, r.stdout or "", r.stderr or "", "chroot")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    # 2. nsenter 进入宿主机 mount+pid+uts+ipc+net (除了 user/cgroup 全进去)
    ns_prefix = "/host/proc/1/ns"
    nsenter_args = ["nsenter"]
    added = 0
    for ns_kind in ("mnt", "pid", "uts", "ipc", "net"):
        p = f"{ns_prefix}/{ns_kind}"
        if os.path.exists(p):
            nsenter_args.extend([f"--{ns_kind}=" + p])
            added += 1
    if added >= 2:
        try:
            full = nsenter_args + ["--", "sh", "-c", cmd_str]
            r = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0 and (r.stdout or r.stderr):
                return (r.returncode, r.stdout or "", r.stderr or "", "nsenter")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    # 3. 容器内本地执行（兜底）—— 注：不再 fallback 到 docker run docker:cli
    #    拉取 174MB 镜像会严重拖慢 Dashboard 加载，且用户已挂载 /:/host，
    #    chroot / nsenter 就能拿到宿主机真实信息。
    try:
        r = subprocess.run(["sh", "-c", cmd_str], capture_output=True, text=True, timeout=timeout)
        return (r.returncode, r.stdout or "", r.stderr or "", "container-local")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        return (1, "", str(e), "container-local-failed")


def _run_in_host_net(cmd: list, timeout: int = 10) -> tuple:
    """尝试在「宿主机网络命名空间」执行给定命令（数组参数，不经过 shell）。
    三层 fallback（从上到下，前一个失败就用下一个）：
      1. nsenter --net=/host/proc/1/ns/net  (需要 /:/host 挂载且 privileged 权限)
      2. chroot /host sh -c '<cmd>'          (需要宿主机存在对应二进制)
      3. 直接在当前进程（容器）执行           (拿到容器内网卡，保底)

    注意：不再用 docker run --network=host alpine 做 fallback，避免首次触发
    alpine 镜像下载（几 MB ~ 几十 MB）导致 Dashboard 首屏加载卡住。
    用户已挂载 /:/host，nsenter / chroot 就能拿到宿主机真实网络信息。

    返回 (returncode, stdout, stderr, source)，source 用于标识最终用了哪层执行。
    """
    cmd_str = " ".join(cmd)
    import os

    # 1. nsenter（最优先：零开销、不需要额外镜像）
    nsenter_netns = "/host/proc/1/ns/net"
    if os.path.exists(nsenter_netns):
        try:
            full = ["nsenter", "--net=" + nsenter_netns, "--"] + list(cmd)
            r = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0 and r.stdout.strip():
                return (r.returncode, r.stdout, r.stderr, "nsenter")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    # 2. chroot /host（用 sh -c 包一层以便宿主机 PATH 解析）
    if os.path.isdir("/host"):
        try:
            full = ["chroot", "/host", "sh", "-c", cmd_str]
            r = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0 and r.stdout.strip():
                return (r.returncode, r.stdout, r.stderr, "chroot")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    # 3. 容器内直接执行（兜底）
    try:
        r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
        return (r.returncode, r.stdout or "", r.stderr or "", "container-local")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        return (1, "", str(e), "container-local-failed")


def get_default_interface() -> str:
    """获取「宿主机」默认外网出口网卡名。
    用户明确要求：只通过以下命令筛选，其余解析逻辑去掉。
        ip route get 8.8.8.8 | awk '/dev/ {print $5}'
    在用户实际环境下直接输出：ens33-ovs

    由于应用通常运行在 Docker 容器（独立 netns），本函数会按以下顺序
    尝试在「宿主机网络命名空间」执行该管道命令，确保拿到的是宿主机真实网卡：
      1. nsenter --net=/host/proc/1/ns/net   （最快、零镜像依赖）
      2. chroot /host sh -c '<cmd>'           （切根到宿主机）
      3. 容器内直接执行（兜底）

    注意：不再用 docker run alpine 做 fallback，避免首次触发镜像下载卡住 UI。
    用户已挂载 /:/host，nsenter / chroot 就够了。
    """
    import os
    # 用户指定的固定管道命令（原样保留，不再 Python 侧解析）
    shell_cmd = "ip route get 8.8.8.8 | awk '/dev/ {print $5}'"

    # 1. nsenter 进入宿主机 netns
    nsenter_netns = "/host/proc/1/ns/net"
    if os.path.exists(nsenter_netns):
        try:
            r = subprocess.run(
                ["nsenter", "--net=" + nsenter_netns, "--", "sh", "-c", shell_cmd],
                capture_output=True, text=True, timeout=15
            )
            out = (r.stdout or "").strip()
            if r.returncode == 0 and out:
                return out
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    # 2. chroot /host
    if os.path.isdir("/host"):
        try:
            r = subprocess.run(
                ["chroot", "/host", "sh", "-c", shell_cmd],
                capture_output=True, text=True, timeout=15
            )
            out = (r.stdout or "").strip()
            if r.returncode == 0 and out:
                return out
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    # 3. 容器本地执行（兜底）
    try:
        r = subprocess.run(
            ["sh", "-c", shell_cmd],
            capture_output=True, text=True, timeout=10
        )
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out:
            return out
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    return ""


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
        "primary_interface": "",  # 默认路由出口网卡名（优先外网真实网卡）
        "network_info": []
    }

    # 先取默认出口网卡，后续给 network_info 打 is_default 标记
    default_iface = get_default_interface()
    info["primary_interface"] = default_iface
    
    # CPU 使用率：两次采样取差值
    # 同步接口用 0.2s 快速返回首屏值；真正的秒级精确值由 SSE 持续推送（1s 间隔）
    try:
        usage = _measure_cpu_usage(interval_sec=0.2)
        if usage is not None:
            info["cpu_usage"] = f"{usage:.1f}%"
        else:
            info["cpu_usage"] = "获取中..."
    except Exception:
        info["cpu_usage"] = "无法获取"

    # 内存：优先读 /host/proc/meminfo（已由 _measure_memory_usage 封装，纯 Python，零 subprocess）
    try:
        mem = _measure_memory_usage()
        if mem:
            info["memory_total"] = mem["memory_total"]
            info["memory_used"]  = mem["memory_used"]
            info["memory_usage"] = mem["memory_usage"]
    except Exception:
        # fallback 到 free -m（极端情况下）
        try:
            result = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if line.startswith('Mem:'):
                        parts = line.split()
                        if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
                            info["memory_total"] = f"{parts[1]} MB"
                            info["memory_used"] = f"{parts[2]} MB"
                            info["memory_usage"] = f"{(int(parts[2]) / int(parts[1]) * 100):.1f}%"
                        break
        except Exception:
            pass
    
    # ── 磁盘空间：优先取宿主机真实分区容量 ────────────────────────────
    # 1) 最快、最准：挂载了 /host 就直接 os.statvfs('/host')，拿的是宿主机根分区容量
    #    （纯 Python，无 subprocess，不会触发 overlay 自身的偏差）
    disk_ok = False
    try:
        import os
        if os.path.isdir("/host"):
            st = os.statvfs("/host")
            block_size = st.f_frsize or st.f_bsize
            total_bytes = st.f_blocks * block_size
            avail_bytes = st.f_bavail * block_size
            used_bytes  = total_bytes - avail_bytes
            if total_bytes > 0:
                def _hr(n):
                    """格式化成和 df -h 一致的人类可读字符串。"""
                    units = ["B", "K", "M", "G", "T", "P"]
                    idx = 0
                    v = float(n)
                    while v >= 1024 and idx < len(units) - 1:
                        v /= 1024
                        idx += 1
                    # df -h 保留一位小数或整数（<10 时带小数）
                    if v < 10 and idx >= 2:
                        return f"{v:.1f}{units[idx]}"
                    return f"{v:.0f}{units[idx]}"
                info["disk_total"] = _hr(total_bytes)
                info["disk_used"]  = _hr(used_bytes)
                pct_used = used_bytes / total_bytes * 100
                info["disk_usage"] = f"{pct_used:.0f}%"
                disk_ok = True
    except Exception:
        disk_ok = False

    # 2) /host 不可用时 fallback：穿透到宿主机跑 df -P /
    if not disk_ok:
        try:
            rc, stdout, stderr, _ = _run_on_host("df -P / 2>/dev/null", timeout=15)
            if rc == 0 and stdout.strip():
                lines = stdout.strip().splitlines()
                if len(lines) >= 2:
                    parts = lines[1].split()
                    # df -P 单位是 1K-blocks
                    total_k = int(parts[1])
                    used_k  = int(parts[2])
                    pct     = parts[4].replace('%', '')
                    if total_k > 0:
                        def _k2hr(k):
                            units = ["K", "M", "G", "T", "P"]
                            idx = 0
                            v = float(k)
                            while v >= 1024 and idx < len(units) - 1:
                                v /= 1024
                                idx += 1
                            if v < 10 and idx >= 1:
                                return f"{v:.1f}{units[idx]}"
                            return f"{v:.0f}{units[idx]}"
                        info["disk_total"] = _k2hr(total_k)
                        info["disk_used"]  = _k2hr(used_k)
                        info["disk_usage"] = pct + '%'
                        disk_ok = True
        except Exception:
            disk_ok = False

    # 3) 再兜底：原来的容器本地 df -h /（overlay，但至少有值）
    if not disk_ok:
        try:
            result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if len(lines) >= 2:
                    parts = lines[1].split()
                    info["disk_total"] = parts[1]
                    info["disk_used"] = parts[2]
                    info["disk_usage"] = parts[4].replace('%', '') + '%'
                    disk_ok = True
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
    
    # ── 系统版本：优先取宿主机真实发行版 ─────────────────────────────
    # 1) /host/etc/os-release 就是宿主机 /etc/os-release
    os_ok = False
    try:
        host_os_release = "/host/etc/os-release"
        if os.path.isfile(host_os_release):
            with open(host_os_release, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if content.strip():
                os_info = {}
                for line in content.strip().split('\n'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        os_info[key] = value.strip().strip('"')
                pretty = os_info.get('PRETTY_NAME') or os_info.get('NAME') or ""
                if pretty:
                    info["os_version"] = pretty
                    os_ok = True
    except Exception:
        os_ok = False

    # 2) fallback：穿透到宿主机执行 cat /etc/os-release
    if not os_ok:
        try:
            rc, stdout, stderr, _ = _run_on_host("cat /etc/os-release 2>/dev/null", timeout=12)
            if rc == 0 and stdout.strip():
                os_info = {}
                for line in stdout.strip().split('\n'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        os_info[key] = value.strip().strip('"')
                pretty = os_info.get('PRETTY_NAME') or os_info.get('NAME') or ""
                if pretty:
                    info["os_version"] = pretty
                    os_ok = True
        except Exception:
            os_ok = False

    # 3) 再兜底：容器内 /etc/os-release + uname -a
    if not os_ok:
        try:
            result = subprocess.run(["cat", "/etc/os-release"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                os_info = {}
                for line in result.stdout.strip().split('\n'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        os_info[key] = value.strip('"')
                info["os_version"] = os_info.get('PRETTY_NAME', os_info.get('NAME', '未知'))
                os_ok = True
        except Exception:
            try:
                result = subprocess.run(["uname", "-a"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    info["os_version"] = result.stdout.strip()
            except Exception:
                pass
    
    try:
        rc, stdout, stderr, src = _run_in_host_net(["ip", "addr"], timeout=8)
        _net_src = src
        if rc == 0 and stdout.strip():
            interfaces = []
            current_iface = None
            for line in stdout.strip().split('\n'):
                if line.startswith(' '):
                    if current_iface and 'inet ' in line:
                        ip = line.split('inet ')[1].split('/')[0]
                        if not ip.startswith('127.'):
                            current_iface["ip"] = ip
                elif ':' in line:
                    if current_iface and current_iface.get("ip"):
                        interfaces.append(current_iface)
                    name = line.split(':')[1].strip()
                    # 如果是容器veth格式，清理@后面部分（仅显示用）
                    clean_name = name.split('@')[0] if '@' in name else name
                    current_iface = {
                        "name": clean_name,
                        "name_raw": name,
                        "ip": "",
                        "is_default": bool(default_iface) and (clean_name == default_iface or name == default_iface)
                    }
            if current_iface and current_iface.get("ip"):
                interfaces.append(current_iface)
            info["network_info"] = interfaces
            info["network_source"] = _net_src
    except Exception:
        try:
            rc, stdout, stderr, src = _run_in_host_net(["ifconfig"], timeout=8)
            _net_src = src
            if rc == 0 and stdout.strip():
                interfaces = []
                current_iface = None
                for line in stdout.strip().split('\n'):
                    if line.strip() and not line.startswith(' '):
                        if current_iface and current_iface.get("ip"):
                            interfaces.append(current_iface)
                        name = line.split(':')[0].strip()
                        clean_name = name.split('@')[0] if '@' in name else name
                        current_iface = {
                            "name": clean_name,
                            "name_raw": name,
                            "ip": "",
                            "is_default": bool(default_iface) and (clean_name == default_iface or name == default_iface)
                        }
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
                info["network_source"] = _net_src
        except Exception:
            info["network_source"] = "failed"
    # 兜底：如果上面两个分支都没拿到 network_info，保持空数组
    info.setdefault("network_source", "fallback_empty")
    
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
    def _reverse_lines(content: str) -> str:
        """按行倒序，最新日志在最上方。保留末尾换行行为与原内容一致。"""
        if not content:
            return content
        # 统一换行并分割；末尾为空串说明原内容以换行结尾，需要剔除用于正确还原
        lines = content.replace("\r\n", "\n").split("\n")
        ends_with_newline = lines and lines[-1] == ""
        if ends_with_newline:
            lines = lines[:-1]
        lines.reverse()
        result = "\n".join(lines)
        if ends_with_newline:
            result += "\n"
        return result

    try:
        # docker logs 会将容器日志输出到 stderr 而非 stdout（Docker 固有行为）
        # 因此无论成功与否，都需要合并 stdout + stderr 两个流
        result = subprocess.run(
            ["docker", "logs", "--tail", str(tail), container_id],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            # 合并两个流：先 stdout 再 stderr，避免漏掉任何输出
            combined_parts = []
            if result.stdout:
                combined_parts.append(result.stdout)
            if result.stderr:
                combined_parts.append(result.stderr)
            if not combined_parts:
                return "暂无日志"
            return _reverse_lines("".join(combined_parts))
        else:
            # 失败时优先返回 stderr（错误信息通常在这里），没有则回退到 stdout
            raw = result.stderr or result.stdout or f"命令执行失败 (exit code {result.returncode})"
            return _reverse_lines(raw) if raw else raw
    except subprocess.TimeoutExpired:
        return "获取日志超时"
    except FileNotFoundError:
        return "Docker命令不可用"
    except Exception as e:
        return f"获取日志失败: {str(e)}"

# ============ Docker 网络相关函数 ============

def list_docker_networks() -> list:
    """列出所有 Docker 网络，返回 [{name, driver, scope}]。"""
    try:
        result = subprocess.run(
            ["docker", "network", "ls", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            return []
        import json
        networks = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                networks.append({
                    "name": item.get("Name", ""),
                    "driver": item.get("Driver", ""),
                    "scope": item.get("Scope", ""),
                })
            except Exception:
                continue
        return networks
    except Exception:
        return []


def create_docker_network(name: str, driver: str = "bridge") -> dict:
    """创建 Docker 网络。返回 {success: bool, message: str, name: str, driver: str}。"""
    if not name or not name.strip():
        return {"success": False, "message": "网络名称不能为空"}
    name = name.strip()
    # 校验名称：允许字母、数字、下划线、中划线、点号，长度 1-128
    import re
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$", name):
        return {"success": False, "message": "网络名称不合法：以字母或数字开头，仅包含字母数字下划线中划线点号，最长128字符"}
    if driver not in ("bridge", "host", "none", "overlay", "ipvlan", "macvlan"):
        driver = "bridge"
    try:
        # 先检查是否已存在
        check = subprocess.run(
            ["docker", "network", "inspect", name],
            capture_output=True,
            text=True,
            timeout=15
        )
        if check.returncode == 0:
            return {"success": False, "message": f"网络 {name} 已存在", "name": name, "driver": driver}

        result = subprocess.run(
            ["docker", "network", "create", "--driver", driver, name],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return {
                "success": True,
                "message": f"网络 {name} 创建成功",
                "name": name,
                "driver": driver,
                "id": (result.stdout or "").strip()
            }
        else:
            err = (result.stderr or result.stdout or "未知错误").strip()
            return {"success": False, "message": f"创建网络失败: {err}", "name": name, "driver": driver}
    except FileNotFoundError:
        return {"success": False, "message": "Docker命令不可用", "name": name, "driver": driver}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "创建网络超时", "name": name, "driver": driver}
    except Exception as e:
        return {"success": False, "message": f"创建网络失败: {str(e)}", "name": name, "driver": driver}

# ============ 容器备份相关函数 ============

BACKUPS_DIR = Path("/app/backup")
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

VOLUMES_DIR = Path("/host/var/lib/docker/volumes")

from .database import (
    add_backup,
    get_all_backups,
    get_backups_by_container,
    delete_backup_by_id,
    get_backup_by_id as db_get_backup_by_id
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
