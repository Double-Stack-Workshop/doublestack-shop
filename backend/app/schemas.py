from pydantic import BaseModel
from typing import List, Literal, Optional

class AddRepoRequest(BaseModel):
    name: Optional[str] = None
    repo_url: str
    branch: str = "main"
    local_path: str = ""
    repo_type: Literal["compose", "script"] = "compose"


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    admin_password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str


class UpdatePasswordRequest(BaseModel):
    password: str


class ForgotPasswordRequest(BaseModel):
    admin_password: str
    new_password: str


class SaveFileRequest(BaseModel):
    content: str


class DeployRequest(BaseModel):
    repo_name: str
    file_name: str


class CreateNetworkRequest(BaseModel):
    name: str
    driver: str = "bridge"


class PullImageRequest(BaseModel):
    image_name: str


class ProxyRequest(BaseModel):
    http_proxy: str = ""
    https_proxy: str = ""


class CurrentRepoRequest(BaseModel):
    repo_name: str = ""


class GlobalDomainRequest(BaseModel):
    global_domain: str = ""


class DockerMirrorsRequest(BaseModel):
    mirrors: List[str]

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
    repo_type: Literal["compose", "script"] = "compose"
