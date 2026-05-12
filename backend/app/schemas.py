from pydantic import BaseModel
from typing import List, Optional

class AddRepoRequest(BaseModel):
    name: Optional[str] = None
    repo_url: str
    branch: str = "main"
    local_path: str

class YmlFile(BaseModel):
    name: str
    path: str
    content: str

class RepoInfo(BaseModel):
    name: str
    url: str
    branch: str
    local_path: str
    yml_files: List[YmlFile]
    last_sync: Optional[str] = None
    status: str = "active"
    repo_dir_name: str = ""