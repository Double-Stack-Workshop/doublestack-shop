"""
日志服务模块
记录项目运行时的所有操作日志
"""
import threading
from datetime import datetime
from typing import List, Dict, Optional

class LogService:
    """日志服务类"""
    
    def __init__(self, max_logs: int = 1000):
        self._logs: List[Dict] = []
        self._lock = threading.Lock()
        self._max_logs = max_logs
    
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
                'timestamp': datetime.now().isoformat(),
                'details': details if details else []
            }
            self._logs.append(log_entry)
            
            # 保持日志数量在限制内
            if len(self._logs) > self._max_logs:
                self._logs = self._logs[-self._max_logs:]
    
    def get_logs(self, level: Optional[str] = None, log_type: Optional[str] = None) -> List[Dict]:
        """
        获取日志列表
        
        Args:
            level: 日志等级过滤
            log_type: 操作类型过滤
            
        Returns:
            日志列表
        """
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
