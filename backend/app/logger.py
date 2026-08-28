"""
日志服务模块
记录项目运行时的所有操作日志
"""
import os
import threading
from pathlib import Path
from typing import List, Dict, Optional

class LogService:
    """日志服务类"""
    
    def __init__(self, max_logs: int = 1000):
        self._logs: List[Dict] = []
        self._lock = threading.Lock()
        self._max_logs = max_logs
        self._use_db = True
        self._db_initialized = False
        self._log_dir = Path(os.getenv('APP_LOG_DIR', '/app/logs'))
        self._log_file = self._log_dir / 'operations.log'
        self._max_file_size = 10 * 1024 * 1024
        self._backup_file_count = 5
        self._file_logging_enabled = self._initialize_log_file()

    def _initialize_log_file(self) -> bool:
        """初始化持久化日志目录；文件异常不影响应用主流程。"""
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._log_file.touch(exist_ok=True)
            return True
        except OSError:
            return False

    def _rotate_log_file(self) -> None:
        """达到上限后轮转操作日志，避免持久化目录无限增长。"""
        if not self._log_file.exists() or self._log_file.stat().st_size < self._max_file_size:
            return

        oldest = self._log_file.with_suffix(f'{self._log_file.suffix}.{self._backup_file_count}')
        if oldest.exists():
            oldest.unlink()
        for index in range(self._backup_file_count - 1, 0, -1):
            source = self._log_file.with_suffix(f'{self._log_file.suffix}.{index}')
            if source.exists():
                source.replace(self._log_file.with_suffix(f'{self._log_file.suffix}.{index + 1}'))
        self._log_file.replace(self._log_file.with_suffix(f'{self._log_file.suffix}.1'))

    def _write_log_file(self, entry: Dict) -> None:
        if not self._file_logging_enabled:
            return
        try:
            self._rotate_log_file()
            details = entry['details']
            detail_text = f" | details: {' || '.join(map(str, details))}" if details else ''
            with self._log_file.open('a', encoding='utf-8') as log_file:
                log_file.write(
                    f"{entry['timestamp']} | {entry['level']} | {entry['type']} | "
                    f"{entry['message']}{detail_text}\n"
                )
        except OSError:
            self._file_logging_enabled = False
    
    def _get_utc8_now_str(self):
        """获取 UTC+8 时间字符串"""
        from datetime import datetime, timezone, timedelta
        return datetime.now(timezone(timedelta(hours=8))).isoformat()
    
    def _ensure_db_initialized(self):
        if not self._db_initialized:
            try:
                self._db_initialized = True
            except Exception:
                self._use_db = False
    
    def _sync_logs_to_memory(self):
        """将数据库中的日志同步到内存"""
        if not self._use_db:
            return
        try:
            from .database import get_operation_logs
            db_logs = get_operation_logs(limit=self._max_logs)
            self._logs = db_logs
        except Exception:
            self._use_db = False
    
    def add_log(self, level: str, message: str, log_type: str = 'system', details: Optional[List[str]] = None) -> None:
        """
        添加日志
        
        Args:
            level: 日志等级 (INFO, WARNING, ERROR, SUCCESS)
            message: 日志消息
            log_type: 操作类型 (deploy, container, image, system)
            details: 详细日志列表（如部署时的镜像拉取日志）
        """
        with self._lock:
            log_entry = {
                'level': level,
                'message': message,
                'type': log_type,
                'timestamp': self._get_utc8_now_str(),
                'details': details if details else []
            }
            self._logs.append(log_entry)
            
            # 保持日志数量在限制内
            if len(self._logs) > self._max_logs:
                self._logs = self._logs[-self._max_logs:]
            self._write_log_file(log_entry)
        
        # 同时写入数据库
        if self._use_db:
            try:
                from .database import add_operation_log
                add_operation_log(level, message, log_type, details)
            except Exception:
                pass
    
    def get_logs(self, level: Optional[str] = None, log_type: Optional[str] = None) -> List[Dict]:
        """
        获取日志列表
        
        Args:
            level: 日志等级过滤
            log_type: 操作类型过滤
            
        Returns:
            日志列表
        """
        if self._use_db and self._db_initialized:
            try:
                from .database import get_operation_logs
                return get_operation_logs(level=level, log_type=log_type, limit=self._max_logs)
            except Exception:
                pass
        
        with self._lock:
            logs = self._logs.copy()
        
        if level:
            logs = [log for log in logs if log['level'] == level]
        if log_type:
            logs = [log for log in logs if log['type'] == log_type]
        
        # 返回最新的在前
        return list(reversed(logs))
    
    def clear_logs(self) -> None:
        """清空所有日志"""
        with self._lock:
            self._logs.clear()
            if self._file_logging_enabled:
                try:
                    for log_file in self._log_dir.glob('operations.log*'):
                        log_file.unlink()
                    self._log_file.touch(exist_ok=True)
                except OSError:
                    self._file_logging_enabled = False
        
        if self._use_db:
            try:
                from .database import clear_operation_logs
                clear_operation_logs()
            except Exception:
                pass
    
    def info(self, message: str, log_type: str = 'system', details: Optional[List[str]] = None) -> None:
        """添加信息日志"""
        self.add_log('INFO', message, log_type, details)
    
    def warning(self, message: str, log_type: str = 'system', details: Optional[List[str]] = None) -> None:
        """添加警告日志"""
        self.add_log('WARNING', message, log_type, details)
    
    def error(self, message: str, log_type: str = 'system', details: Optional[List[str]] = None) -> None:
        """添加错误日志"""
        self.add_log('ERROR', message, log_type, details)
    
    def success(self, message: str, log_type: str = 'system', details: Optional[List[str]] = None) -> None:
        """添加成功日志"""
        self.add_log('SUCCESS', message, log_type, details)

# 全局日志服务实例
log_service = LogService()
