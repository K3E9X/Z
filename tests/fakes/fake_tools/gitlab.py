"""Fake GitLab: REST v4 subset (`/gitlab/api/v4`), MCP server (`/gitlab/mcp`) and git smart HTTP for the seeded
repositories (`/gitlab/<group>/<project>.git`). Repositories are real bare git repos (dulwich) in a temp dir.

Auth: REST and MCP accept `Authorization: Bearer <token>` or `PRIVATE-TOKEN: <token>`; git accepts HTTP basic
(`oauth2:<token>`, any user name) or bearer. Clone/fetch needs the read or write token, push needs the write token.
"""

import base64
import difflib
import gzip
import shutil
import tempfile
import threading
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import unquote

import anyio
from dulwich.object_store import iter_tree_contents, tree_lookup_path
from dulwich.objects import Blob, Commit, Tree
from dulwich.protocol import ReceivableProtocol
from dulwich.repo import Repo
from dulwich.server import DictBackend, ReceivePackHandler, UploadPackHandler
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .._mcp import McpTool, TokenKind
from .errors import BadRequest, Conflict, NotFound
from .state import Store, extract_token, now_iso

AUTHOR = b"Fake GitLab <gitlab@example.test>"
SEED_TIME = 1760000000
FileSpec = str | dict[str, str]


def _blob_mode(spec: FileSpec) -> tuple[bytes, int]:
    if isinstance(spec, dict):
        return spec["content"].encode(), 0o100755 if spec.get("mode") == "755" else 0o100644
    return spec.encode(), 0o100644


def _write_tree(repo: Repo, files: dict[str, tuple[bytes, int]]) -> bytes:
    tree = Tree()
    subdirs: dict[str, dict[str, tuple[bytes, int]]] = defaultdict(dict)
    for path, (content, mode) in files.items():
        head, _, rest = path.partition("/")
        if rest:
            subdirs[head][rest] = (content, mode)
        else:
            blob = Blob.from_string(content)
            repo.object_store.add_object(blob)
            tree.add(head.encode(), mode, blob.id)
    for name, sub in subdirs.items():
        tree.add(name.encode(), 0o040000, _write_tree(repo, sub))
    repo.object_store.add_object(tree)
    return tree.id


def _commit(repo: Repo, files: dict[str, FileSpec], parents: list[bytes], message: str) -> bytes:
    c = Commit()
    c.tree = _write_tree(repo, {p: _blob_mode(s) for p, s in files.items()})
    c.parents = parents
    c.author = c.committer = AUTHOR
    c.author_time = c.commit_time = SEED_TIME + len(parents)
    c.author_timezone = c.commit_timezone = 0
    c.encoding = b"UTF-8"
    c.message = message.encode() + b"\n"
    repo.object_store.add_object(c)
    return c.id


class GitLab:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.root = Path(tempfile.mkdtemp(prefix="fake-gitlab-"))
        self.repos: dict[int, Repo] = {}
        self.git_lock = threading.Lock()
        store.on_seed.append(self._on_seed)

    @property
    def data(self) -> dict[str, Any]:
        result: dict[str, Any] = self.store.data["gitlab"]
        return result

    # ---- seed ----
    def _on_seed(self, seed: dict[str, Any]) -> None:
        if "gitlab" not in seed:
            return
        with self.git_lock:
            for repo in self.repos.values():
                repo.close()
            shutil.rmtree(self.root, ignore_errors=True)
            self.root.mkdir(parents=True)
            self.repos = {}
            for p in self.data["projects"]:
                path = self.root / f"{p['id']}.git"
                repo = Repo.init_bare(str(path), mkdir=True)
                main = _commit(repo, p.get("files", {}), [], "Initial commit")
                repo.refs[b"refs/heads/main"] = main
                repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")
                for branch, changes in p.get("branches", {}).items():
                    sha = _commit(repo, {**p.get("files", {}), **changes}, [main], f"Update {branch}")
                    repo.refs[f"refs/heads/{branch}".encode()] = sha
                self.repos[p["id"]] = repo
        for i, mr in enumerate(self.data.setdefault("merge_requests", [])):
            mr.setdefault("id", 5000 + i)
            mr.setdefault("created_at", now_iso(SEED_TIME))
            mr.setdefault("labels", [])
            mr.setdefault("description", "")
            mr.setdefault("author", {"username": "bob", "name": "Bob Durand"})
        self.data.setdefault("issues", [])
        self.data.setdefault("notes", [])

    def close(self) -> None:
        for repo in self.repos.values():
            repo.close()
        shutil.rmtree(self.root, ignore_errors=True)

    # ---- lookups ----
    def project(self, ref: str | int) -> dict[str, Any]:
        key = unquote(str(ref))
        for p in self.data["projects"]:
            if key in (str(p["id"]), p["path"]):
                return p
        raise NotFound("404 Project Not Found", {"message": "404 Project Not Found"})

    def project_json(self, p: dict[str, Any]) -> dict[str, Any]:
        base = self.store.base_url
        ns, _, name = p["path"].rpartition("/")
        group = next((g for g in self.data.get("groups", []) if g["full_path"] == ns), {"id": 0, "name": ns})
        return {
            "id": p["id"],
            "name": name,
            "path": name,
            "description": p.get("description", ""),
            "path_with_namespace": p["path"],
            "name_with_namespace": f"{ns} / {name}",
            "namespace": {"id": group["id"], "name": group["name"], "path": ns, "full_path": ns, "kind": "group"},
            "default_branch": "main",
            "visibility": "internal",
            "archived": False,
            "http_url_to_repo": f"{base}/gitlab/{p['path']}.git",
            "web_url": f"{base}/gitlab/{p['path']}",
            "ssh_url_to_repo": f"git@gitlab.example.test:{p['path']}.git",
            "created_at": now_iso(SEED_TIME),
            "last_activity_at": now_iso(SEED_TIME),
        }

    def repo(self, p: dict[str, Any]) -> Repo:
        return self.repos[p["id"]]

    def resolve(self, p: dict[str, Any], ref: str | None) -> bytes:
        repo = self.repo(p)
        ref = ref or "main"
        for candidate in (f"refs/heads/{ref}", f"refs/tags/{ref}", ref):
            try:
                if candidate.startswith("refs/"):
                    return repo.refs[candidate.encode()]
                if len(candidate) == 40 and candidate.encode() in repo.object_store:
                    return candidate.encode()
            except KeyError:
                continue
        raise NotFound("404 Commit Not Found", {"message": "404 Commit Not Found"})

    def branches(self, p: dict[str, Any]) -> dict[str, bytes]:
        refs = self.repo(p).refs.as_dict(b"refs/heads")
        return {k.decode(): v for k, v in refs.items()}

    def files_at(self, p: dict[str, Any], sha: bytes) -> dict[str, bytes]:
        repo = self.repo(p)
        commit = repo[sha]
        assert isinstance(commit, Commit)
        out: dict[str, bytes] = {}
        for entry in iter_tree_contents(repo.object_store, commit.tree):
            if entry.path is not None and entry.sha is not None:
                blob = repo[entry.sha]
                assert isinstance(blob, Blob)
                out[entry.path.decode()] = blob.data
        return out

    def file(self, project: str, file_path: str, ref: str | None) -> dict[str, Any]:
        p = self.project(project)
        sha = self.resolve(p, ref)
        repo = self.repo(p)
        commit = repo[sha]
        assert isinstance(commit, Commit)
        path = unquote(file_path).strip("/")
        try:
            _mode, blob_id = tree_lookup_path(repo.__getitem__, commit.tree, path.encode())
        except KeyError as exc:
            raise NotFound("404 File Not Found", {"message": "404 File Not Found"}) from exc
        obj = repo[blob_id]
        if not isinstance(obj, Blob):
            raise NotFound("404 File Not Found", {"message": "404 File Not Found"})
        return {
            "file_name": path.rsplit("/", 1)[-1],
            "file_path": path,
            "size": len(obj.data),
            "encoding": "base64",
            "content": base64.b64encode(obj.data).decode(),
            "ref": ref or "main",
            "blob_id": blob_id.decode(),
            "commit_id": sha.decode(),
            "last_commit_id": sha.decode(),
            "_raw": obj.data,
        }

    def tree(self, project: str, path: str, ref: str | None) -> list[dict[str, Any]]:
        p = self.project(project)
        files = self.files_at(p, self.resolve(p, ref))
        prefix = path.strip("/") + "/" if path.strip("/") else ""
        entries: dict[str, str] = {}
        for f in files:
            if f.startswith(prefix):
                head, _, rest = f[len(prefix) :].partition("/")
                entries[head] = "tree" if rest else "blob"
        return [
            {"name": n, "type": t, "path": prefix + n, "mode": "040000" if t == "tree" else "100644"}
            for n, t in sorted(entries.items())
        ]

    # ---- merge requests ----
    def _mr(self, p: dict[str, Any], iid: int) -> dict[str, Any]:
        for mr in self.data["merge_requests"]:
            if mr["project_id"] == p["id"] and mr["iid"] == iid:
                return mr
        raise NotFound("404 Not found", {"message": "404 Not found"})

    def mr_json(self, p: dict[str, Any], mr: dict[str, Any]) -> dict[str, Any]:
        branches = self.branches(p)
        sha = branches.get(mr["source_branch"], b"").decode() or mr.get("merge_commit_sha")
        return {
            **{k: v for k, v in mr.items() if not k.startswith("_")},
            "sha": sha,
            "web_url": f"{self.store.base_url}/gitlab/{p['path']}/-/merge_requests/{mr['iid']}",
            "references": {"short": f"!{mr['iid']}", "full": f"{p['path']}!{mr['iid']}"},
            "merge_status": "can_be_merged" if mr["state"] == "opened" else "unchecked",
            "detailed_merge_status": "mergeable" if mr["state"] == "opened" else "not_open",
            "draft": False,
        }

    def list_mrs(self, project: str, state: str = "all") -> list[dict[str, Any]]:
        p = self.project(project)
        return [
            self.mr_json(p, m)
            for m in self.data["merge_requests"]
            if m["project_id"] == p["id"] and state in ("all", m["state"])
        ]

    def get_mr(self, project: str, iid: int) -> dict[str, Any]:
        p = self.project(project)
        return self.mr_json(p, self._mr(p, iid))

    def diffs(self, project: str, iid: int) -> list[dict[str, Any]]:
        p = self.project(project)
        mr = self._mr(p, iid)
        branches = self.branches(p)
        new_sha = branches.get(mr["source_branch"]) or mr.get("_source_sha", "").encode()
        old_sha = mr.get("_target_sha", "").encode() or branches.get(mr["target_branch"])
        if not new_sha or not old_sha:
            return []
        old, new = self.files_at(p, old_sha), self.files_at(p, new_sha)
        out = []
        for path in sorted(set(old) | set(new)):
            if old.get(path) == new.get(path):
                continue
            a = old.get(path, b"").decode(errors="replace").splitlines()
            b = new.get(path, b"").decode(errors="replace").splitlines()
            hunks = list(difflib.unified_diff(a, b, lineterm=""))[2:]
            out.append(
                {
                    "old_path": path,
                    "new_path": path,
                    "a_mode": "100644",
                    "b_mode": "100644",
                    "new_file": path not in old,
                    "deleted_file": path not in new,
                    "renamed_file": False,
                    "diff": "\n".join(hunks) + "\n",
                }
            )
        return out

    def create_mr(self, project: str, body: dict[str, Any], kind: TokenKind) -> dict[str, Any]:
        p = self.project(project)
        src, dst, title = body.get("source_branch"), body.get("target_branch") or "main", body.get("title")
        if not src or not title:
            raise BadRequest("source_branch and title are required", {"error": "source_branch, title are missing"})
        branches = self.branches(p)
        if src not in branches:
            raise BadRequest(
                f'Source branch "{src}" does not exist', {"message": [f'Source branch "{src}" does not exist']}
            )
        if dst not in branches:
            raise BadRequest(
                f'Target branch "{dst}" does not exist', {"message": [f'Target branch "{dst}" does not exist']}
            )
        if src == dst:
            raise BadRequest(
                "source and target branch are the same",
                {"message": ["You can't use same project/branch for source and target"]},
            )
        with self.store.lock:
            for mr in self.data["merge_requests"]:
                if mr["project_id"] == p["id"] and mr["source_branch"] == src and mr["state"] == "opened":
                    msg = f"Another open merge request already exists for this source branch: !{mr['iid']}"
                    raise Conflict(msg, {"message": [msg]})
            iid = max((m["iid"] for m in self.data["merge_requests"] if m["project_id"] == p["id"]), default=0) + 1
            labels = body.get("labels") or []
            mr = {
                "id": 5000 + self.store.next_id(),
                "iid": iid,
                "project_id": p["id"],
                "title": title,
                "description": body.get("description", ""),
                "state": "opened",
                "source_branch": src,
                "target_branch": dst,
                "labels": labels.split(",") if isinstance(labels, str) else labels,
                "author": {"username": "z-bot", "name": "Z Service"},
                "created_at": now_iso(),
            }
            self.data["merge_requests"].append(mr)
        self.store.record(
            "gitlab",
            "gitlab.merge_requests.create",
            kind,
            {
                "project": p["path"],
                "iid": iid,
                "source_branch": src,
                "target_branch": dst,
                "title": title,
                "sha": branches[src].decode(),
            },
        )
        return self.mr_json(p, mr)

    def merge_mr(self, project: str, iid: int, body: dict[str, Any], kind: TokenKind) -> dict[str, Any]:
        p = self.project(project)
        with self.store.lock, self.git_lock:
            mr = self._mr(p, iid)
            if mr["state"] != "opened":
                raise BadRequest("405 Method Not Allowed", {"message": "405 Method Not Allowed"})
            repo = self.repo(p)
            src_ref = f"refs/heads/{mr['source_branch']}".encode()
            sha = repo.refs[src_ref]
            mr["_target_sha"] = repo.refs[f"refs/heads/{mr['target_branch']}".encode()].decode()
            mr["_source_sha"] = sha.decode()
            repo.refs[f"refs/heads/{mr['target_branch']}".encode()] = sha
            mr.update(state="merged", merged_at=now_iso(), merge_commit_sha=sha.decode())
            if body.get("should_remove_source_branch"):
                del repo.refs[src_ref]
        self.store.record("gitlab", "gitlab.merge_requests.merge", kind, {"project": p["path"], "iid": iid})
        return self.mr_json(p, mr)

    def delete_branch(self, project: str, branch: str, kind: TokenKind) -> None:
        p = self.project(project)
        branch = unquote(branch)
        if branch == "main":
            raise BadRequest("Cannot remove the default branch", {"message": "405 Method Not Allowed"})
        with self.git_lock:
            ref = f"refs/heads/{branch}".encode()
            repo = self.repo(p)
            if ref not in repo.refs:
                raise NotFound("404 Branch Not Found", {"message": "404 Branch Not Found"})
            del repo.refs[ref]
        self.store.record("gitlab", "gitlab.branches.delete", kind, {"project": p["path"], "branch": branch})

    # ---- issues and notes ----
    def create_issue(self, project: str, body: dict[str, Any], kind: TokenKind) -> dict[str, Any]:
        p = self.project(project)
        if not body.get("title"):
            raise BadRequest("title is missing", {"error": "title is missing"})
        with self.store.lock:
            iid = max((i["iid"] for i in self.data["issues"] if i["project_id"] == p["id"]), default=0) + 1
            labels = body.get("labels") or []
            issue = {
                "id": 7000 + self.store.next_id(),
                "iid": iid,
                "project_id": p["id"],
                "title": body["title"],
                "description": body.get("description", ""),
                "state": "opened",
                "labels": labels.split(",") if isinstance(labels, str) else labels,
                "created_at": now_iso(),
                "web_url": f"{self.store.base_url}/gitlab/{p['path']}/-/issues/{iid}",
            }
            self.data["issues"].append(issue)
        self.store.record(
            "gitlab", "gitlab.issues.create", kind, {"project": p["path"], "iid": iid, "title": body["title"]}
        )
        return issue

    def create_note(self, project: str, noteable: str, iid: int, body: str, kind: TokenKind) -> dict[str, Any]:
        p = self.project(project)
        if noteable == "merge_request":
            self._mr(p, iid)
        elif not any(i["project_id"] == p["id"] and i["iid"] == iid for i in self.data["issues"]):
            raise NotFound("404 Issue Not Found", {"message": "404 Issue Not Found"})
        if not body:
            raise BadRequest("body is missing", {"error": "body is missing"})
        note = {
            "id": 9000 + self.store.next_id(),
            "body": body,
            "project_id": p["id"],
            "noteable_type": "MergeRequest" if noteable == "merge_request" else "Issue",
            "noteable_iid": iid,
            "author": {"username": "z-bot", "name": "Z Service"},
            "created_at": now_iso(),
            "system": False,
        }
        with self.store.lock:
            self.data["notes"].append(note)
        self.store.record(
            "gitlab",
            "gitlab.notes.create",
            kind,
            {"project": p["path"], "noteable_type": noteable, "noteable_iid": iid, "body": body},
        )
        return note

    # ---- git smart HTTP ----
    def git_project(self, repo_path: str) -> dict[str, Any]:
        path = repo_path.strip("/")
        return self.project(path.removesuffix(".git"))

    def advertise(self, p: dict[str, Any], service: str) -> bytes:
        out = BytesIO()
        handler_cls = UploadPackHandler if service == "git-upload-pack" else ReceivePackHandler
        proto = ReceivableProtocol(BytesIO().read, out.write)
        handler = handler_cls(DictBackend({"/": self.repo(p)}), ["/"], proto, stateless_rpc=True, advertise_refs=True)
        handler.proto.write_pkt_line(f"# service={service}\n".encode())
        handler.proto.write_pkt_line(None)
        handler.handle()
        return out.getvalue()

    def serve(self, p: dict[str, Any], service: str, body: bytes, kind: TokenKind) -> bytes:
        out = BytesIO()
        proto = ReceivableProtocol(BytesIO(body).read, out.write)
        if service == "git-upload-pack":
            UploadPackHandler(DictBackend({"/": self.repo(p)}), ["/"], proto, stateless_rpc=True).handle()
            return out.getvalue()
        with self.git_lock:
            before = self.branches(p)
            ReceivePackHandler(DictBackend({"/": self.repo(p)}), ["/"], proto, stateless_rpc=True).handle()
            after = self.branches(p)
        updates = [
            {
                "ref": f"refs/heads/{b}",
                "old": before[b].decode() if b in before else None,
                "new": after[b].decode() if b in after else None,
            }
            for b in sorted(set(before) | set(after))
            if before.get(b) != after.get(b)
        ]
        self.store.record("gitlab", "gitlab.push", kind, {"project": p["path"], "updates": updates})
        return out.getvalue()


class GitLabPathMiddleware:
    """Keep URL-encoded slashes (`infra%2Fpayments-api`, `src%2FApp.java`) inside one path segment, as GitLab
    does, by routing on the raw path for `/gitlab/api/`."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/gitlab/api/"):
            raw = (scope.get("raw_path") or scope["path"].encode()).decode("latin-1").split("?", 1)[0]
            scope = {**scope, "path": unquote(raw.replace("%2F", "%252F").replace("%2f", "%252F"))}
        await self.app(scope, receive, send)


def _paged(items: list[dict[str, Any]], page: int, per_page: int) -> JSONResponse:
    per_page = max(1, min(per_page, 100))
    page = max(1, page)
    chunk = items[(page - 1) * per_page : page * per_page]
    headers = {
        "X-Total": str(len(items)),
        "X-Page": str(page),
        "X-Per-Page": str(per_page),
        "X-Next-Page": str(page + 1) if page * per_page < len(items) else "",
    }
    return JSONResponse(chunk, headers=headers)


def router(store: Store, gl: GitLab) -> APIRouter:
    r = APIRouter(prefix="/gitlab")
    read = Depends(store.dep("gitlab", "gitlab", "read"))
    write = store.dep("gitlab", "gitlab", "write")
    api = "/api/v4/projects/{project}"

    @r.get("/api/v4/projects", dependencies=[read])
    def projects(search: str = "", page: int = 1, per_page: int = 20) -> JSONResponse:
        items = [gl.project_json(p) for p in gl.data["projects"] if search.lower() in p["path"].lower()]
        return _paged(items, page, per_page)

    @r.get("/api/v4/groups", dependencies=[read])
    def groups() -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = gl.data.get("groups", [])
        return result

    @r.get("/api/v4/groups/{group}/projects", dependencies=[read])
    def group_projects(group: str, search: str = "", page: int = 1, per_page: int = 20) -> JSONResponse:
        g = unquote(group)
        found = next((x for x in gl.data.get("groups", []) if g in (str(x["id"]), x["full_path"])), None)
        if found is None:
            return JSONResponse({"message": "404 Group Not Found"}, status_code=404)
        items = [
            gl.project_json(p)
            for p in gl.data["projects"]
            if p["path"].startswith(found["full_path"] + "/") and search.lower() in p["path"].lower()
        ]
        return _paged(items, page, per_page)

    @r.get(api, dependencies=[read])
    def project(project: str) -> dict[str, Any]:
        return gl.project_json(gl.project(project))

    @r.get(api + "/repository/files/{file_path}", dependencies=[read], response_model=None)
    def file(project: str, file_path: str, ref: str | None = None) -> Response:
        if not ref:
            return JSONResponse({"error": "ref is missing"}, status_code=400)
        data = gl.file(project, file_path, ref)
        data.pop("_raw")
        return JSONResponse(data)

    @r.get(api + "/repository/files/{file_path}/raw", dependencies=[read])
    def file_raw(project: str, file_path: str, ref: str | None = None) -> Response:
        return Response(gl.file(project, file_path, ref)["_raw"], media_type="text/plain")

    @r.get(api + "/repository/tree", dependencies=[read])
    def tree(project: str, path: str = "", ref: str | None = None) -> list[dict[str, Any]]:
        return gl.tree(project, path, ref)

    @r.get(api + "/repository/branches", dependencies=[read])
    def branches(project: str) -> list[dict[str, Any]]:
        p = gl.project(project)
        return [
            {"name": n, "commit": {"id": s.decode()}, "default": n == "main", "protected": n == "main"}
            for n, s in sorted(gl.branches(p).items())
        ]

    @r.delete(api + "/repository/branches/{branch}", status_code=204)
    def delete_branch(project: str, branch: str, kind: Annotated[TokenKind, Depends(write)]) -> Response:
        gl.delete_branch(project, branch, kind)
        return Response(status_code=204)

    @r.get(api + "/merge_requests", dependencies=[read])
    def mrs(project: str, state: str = "all") -> list[dict[str, Any]]:
        return gl.list_mrs(project, state)

    @r.get(api + "/merge_requests/{iid}", dependencies=[read])
    def mr(project: str, iid: int) -> dict[str, Any]:
        return gl.get_mr(project, iid)

    @r.get(api + "/merge_requests/{iid}/changes", dependencies=[read])
    def mr_changes(project: str, iid: int) -> dict[str, Any]:
        return {**gl.get_mr(project, iid), "changes": gl.diffs(project, iid)}

    @r.get(api + "/merge_requests/{iid}/diffs", dependencies=[read])
    def mr_diffs(project: str, iid: int) -> list[dict[str, Any]]:
        return gl.diffs(project, iid)

    @r.post(api + "/merge_requests", status_code=201)
    async def create_mr(project: str, request: Request, kind: Annotated[TokenKind, Depends(write)]) -> dict[str, Any]:
        return gl.create_mr(project, await _body(request), kind)

    @r.put(api + "/merge_requests/{iid}/merge")
    async def merge(
        project: str, iid: int, request: Request, kind: Annotated[TokenKind, Depends(write)]
    ) -> dict[str, Any]:
        return gl.merge_mr(project, iid, await _body(request), kind)

    @r.get(api + "/issues", dependencies=[read])
    def issues(project: str) -> list[dict[str, Any]]:
        p = gl.project(project)
        return [i for i in gl.data["issues"] if i["project_id"] == p["id"]]

    @r.post(api + "/issues", status_code=201)
    async def create_issue(
        project: str, request: Request, kind: Annotated[TokenKind, Depends(write)]
    ) -> dict[str, Any]:
        return gl.create_issue(project, await _body(request), kind)

    @r.post(api + "/merge_requests/{iid}/notes", status_code=201)
    async def mr_note(
        project: str, iid: int, request: Request, kind: Annotated[TokenKind, Depends(write)]
    ) -> dict[str, Any]:
        return gl.create_note(project, "merge_request", iid, (await _body(request)).get("body", ""), kind)

    @r.post(api + "/issues/{iid}/notes", status_code=201)
    async def issue_note(
        project: str, iid: int, request: Request, kind: Annotated[TokenKind, Depends(write)]
    ) -> dict[str, Any]:
        return gl.create_note(project, "issue", iid, (await _body(request)).get("body", ""), kind)

    # ---- git smart HTTP ----
    def git_auth(request: Request, service: str) -> TokenKind | Response:
        token_kind = store.kind("gitlab", _git_token(request))
        what = f"git {service} {request.url.path}"
        if token_kind is None:
            store.deny("gitlab", what, "missing or unknown token")
            return PlainTextResponse(
                "HTTP Basic: Access denied", status_code=401, headers={"WWW-Authenticate": 'Basic realm="GitLab"'}
            )
        if service == "git-receive-pack" and token_kind != "write":
            store.deny("gitlab", what, "read token used for a push")
            return PlainTextResponse("You are not allowed to push code to this project.", status_code=403)
        return token_kind

    @r.get("/{repo_path:path}/info/refs", response_model=None)
    async def info_refs(repo_path: str, request: Request, service: str = "") -> Response:
        if service not in ("git-upload-pack", "git-receive-pack"):
            return PlainTextResponse("dumb HTTP is not supported", status_code=403)
        auth = git_auth(request, service)
        if isinstance(auth, Response):
            return auth
        p = gl.git_project(repo_path)
        payload = await anyio.to_thread.run_sync(gl.advertise, p, service)
        return Response(
            payload, media_type=f"application/x-{service}-advertisement", headers={"Cache-Control": "no-cache"}
        )

    @r.post("/{repo_path:path}/{service}", response_model=None)
    async def git_service(repo_path: str, service: str, request: Request) -> Response:
        if service not in ("git-upload-pack", "git-receive-pack"):
            return PlainTextResponse("not found", status_code=404)
        auth = git_auth(request, service)
        if isinstance(auth, Response):
            return auth
        p = gl.git_project(repo_path)
        body = await request.body()
        if request.headers.get("content-encoding") == "gzip":
            body = gzip.decompress(body)
        payload = await anyio.to_thread.run_sync(gl.serve, p, service, body, auth)
        return Response(payload, media_type=f"application/x-{service}-result", headers={"Cache-Control": "no-cache"})

    return r


def _git_token(request: Request) -> str | None:
    return extract_token("git", request.headers)


async def _body(request: Request) -> dict[str, Any]:
    if request.headers.get("content-type", "").startswith("application/json"):
        data = await request.json()
        return data if isinstance(data, dict) else {}
    form = await request.form()
    return {k: str(v) for k, v in form.items()} | dict(request.query_params)


def mcp_tools(gl: GitLab) -> list[McpTool]:
    pid = {"type": "string", "description": "Project id or full path, e.g. infra/payments-api"}
    iid = {"type": "integer", "minimum": 1, "description": "Merge request IID"}

    def obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": required, "additionalProperties": False}

    def list_projects(a: dict[str, Any], _k: TokenKind) -> dict[str, Any]:
        items = [
            gl.project_json(p)
            for p in gl.data["projects"]
            if a.get("search", "").lower() in p["path"].lower()
            and (not a.get("group") or p["path"].startswith(a["group"].strip("/") + "/"))
        ]
        page, per = a.get("page", 1), a.get("per_page", 20)
        return {"projects": items[(page - 1) * per : page * per], "total": len(items)}

    def get_file(a: dict[str, Any], _k: TokenKind) -> dict[str, Any]:
        path = a.get("file_path", "").strip("/")
        try:
            data = gl.file(a["project_id"], path, a.get("ref"))
        except NotFound:
            entries = gl.tree(a["project_id"], path, a.get("ref"))
            if not entries:
                raise
            return {"type": "tree", "path": path, "entries": entries}
        raw: bytes = data.pop("_raw")
        return {**data, "encoding": "text", "content": raw.decode(errors="replace")}

    def note(a: dict[str, Any], k: TokenKind) -> dict[str, Any]:
        return gl.create_note(a["project_id"], a["noteable_type"], a["noteable_iid"], a["body"], k)

    def delete_branch(a: dict[str, Any], k: TokenKind) -> dict[str, Any]:
        gl.delete_branch(a["project_id"], a["branch"], k)
        return {"deleted": a["branch"]}

    return [
        McpTool(
            "list_projects",
            "List GitLab projects, optionally filtered by group path or search text.",
            obj(
                {
                    "search": {"type": "string"},
                    "group": {"type": "string", "description": "e.g. infra"},
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                    "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                },
                [],
            ),
            list_projects,
        ),
        McpTool(
            "get_project",
            "Get a GitLab project.",
            obj({"project_id": pid}, ["project_id"]),
            lambda a, k: gl.project_json(gl.project(a["project_id"])),
        ),
        McpTool(
            "get_file_contents",
            "Get a file (decoded text) or a directory listing from a repository.",
            obj(
                {
                    "project_id": pid,
                    "file_path": {"type": "string"},
                    "ref": {"type": "string", "description": "Branch, tag or commit (default main)"},
                },
                ["project_id", "file_path"],
            ),
            get_file,
        ),
        McpTool(
            "list_merge_requests",
            "List merge requests of a project.",
            obj(
                {
                    "project_id": pid,
                    "state": {"type": "string", "enum": ["opened", "closed", "merged", "all"], "default": "all"},
                },
                ["project_id"],
            ),
            lambda a, k: {"merge_requests": gl.list_mrs(a["project_id"], a.get("state", "all"))},
        ),
        McpTool(
            "get_merge_request",
            "Get one merge request.",
            obj({"project_id": pid, "merge_request_iid": iid}, ["project_id", "merge_request_iid"]),
            lambda a, k: gl.get_mr(a["project_id"], a["merge_request_iid"]),
        ),
        McpTool(
            "get_merge_request_diffs",
            "Get the file diffs of a merge request.",
            obj({"project_id": pid, "merge_request_iid": iid}, ["project_id", "merge_request_iid"]),
            lambda a, k: {"diffs": gl.diffs(a["project_id"], a["merge_request_iid"])},
        ),
        McpTool(
            "create_merge_request",
            "Create a merge request from a pushed branch.",
            obj(
                {
                    "project_id": pid,
                    "source_branch": {"type": "string"},
                    "target_branch": {"type": "string", "default": "main"},
                    "title": {"type": "string", "minLength": 1},
                    "description": {"type": "string"},
                    "labels": {"type": "array", "items": {"type": "string"}},
                },
                ["project_id", "source_branch", "title"],
            ),
            lambda a, k: gl.create_mr(a["project_id"], a, k),
            access="write",
        ),
        McpTool(
            "create_issue",
            "Create a GitLab issue.",
            obj(
                {
                    "project_id": pid,
                    "title": {"type": "string", "minLength": 1},
                    "description": {"type": "string"},
                    "labels": {"type": "array", "items": {"type": "string"}},
                },
                ["project_id", "title"],
            ),
            lambda a, k: gl.create_issue(a["project_id"], a, k),
            access="write",
        ),
        McpTool(
            "create_note",
            "Add a comment (note) to a merge request or an issue.",
            obj(
                {
                    "project_id": pid,
                    "noteable_type": {"type": "string", "enum": ["merge_request", "issue"]},
                    "noteable_iid": {"type": "integer", "minimum": 1},
                    "body": {"type": "string", "minLength": 1},
                },
                ["project_id", "noteable_type", "noteable_iid", "body"],
            ),
            note,
            access="write",
        ),
        McpTool(
            "merge_merge_request",
            "Merge a merge request.",
            obj(
                {
                    "project_id": pid,
                    "merge_request_iid": iid,
                    "should_remove_source_branch": {"type": "boolean", "default": False},
                },
                ["project_id", "merge_request_iid"],
            ),
            lambda a, k: gl.merge_mr(a["project_id"], a["merge_request_iid"], a, k),
            access="destructive",
        ),
        McpTool(
            "delete_branch",
            "Delete a repository branch.",
            obj({"project_id": pid, "branch": {"type": "string"}}, ["project_id", "branch"]),
            delete_branch,
            access="destructive",
        ),
    ]
