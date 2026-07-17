"""Project asset and lightweight file-browser routes."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from agent_workbench.models.project_asset import ASSET_TYPES, ProjectAssetRepository
from agent_workbench.models.workspace import WorkspaceRepository
from agent_workbench.services.asset_service import AssetService
from agent_workbench.web.app import get_db

bp = Blueprint("assets", __name__, url_prefix="/projects")


def _workspace_or_404(workspace_id: str):
    workspace = WorkspaceRepository(get_db()).get_by_id(workspace_id)
    if workspace is None:
        abort(404, description="Project not found")
    return workspace


def _asset_or_404(workspace_id: str, asset_id: str):
    asset = ProjectAssetRepository(get_db()).get_by_id(asset_id)
    if asset is None or asset.workspace_id != workspace_id:
        abort(404, description="Asset not found")
    return asset


def _safe_listing(asset, relative: str = ""):
    root = Path(asset.path).expanduser().resolve()
    if not root.exists():
        return root, None, [], "Linked path is currently unavailable."
    if root.is_file():
        return root, root, [], ""
    relative = (relative or "").strip().replace("\\", "/")
    candidate = (root / relative).resolve() if relative else root
    try:
        candidate.relative_to(root)
    except ValueError:
        abort(400, description="Path must remain inside the linked asset")
    if not candidate.exists() or not candidate.is_dir():
        abort(404, description="Folder not found")
    entries = []
    try:
        children = sorted(candidate.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        return root, candidate, [], f"Cannot read folder: {exc}"
    for child in children[:200]:
        try:
            stat = child.stat()
            entries.append({
                "name": child.name,
                "is_dir": child.is_dir(),
                "size": stat.st_size if child.is_file() else None,
                "relative": str(child.relative_to(root)),
            })
        except OSError:
            continue
    return root, candidate, entries, ""


@bp.route("/<workspace_id>/assets", methods=["GET", "POST"])
def project_assets(workspace_id: str):
    workspace = _workspace_or_404(workspace_id)
    if request.method == "POST":
        try:
            AssetService(get_db()).add_asset(
                workspace_id=workspace_id,
                asset_type=request.form.get("asset_type", "directory"),
                path=request.form.get("path", "").strip(),
                label=request.form.get("label", "").strip(),
                description=request.form.get("description", "").strip(),
                session_id=request.form.get("session_id") or None,
                agent_id=request.form.get("agent_id") or None,
            )
            flash("Asset linked to this project.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("assets.project_assets", workspace_id=workspace_id))

    assets = AssetService(get_db()).list_assets(workspace_id)
    return render_template(
        "assets.html",
        workspace=workspace,
        assets=assets,
        asset_types=ASSET_TYPES,
        selected_asset=None,
        entries=[],
        current_relative="",
        browser_error="",
        root=None,
        current_folder=None,
    )


@bp.route("/<workspace_id>/assets/<asset_id>")
def browse_asset(workspace_id: str, asset_id: str):
    workspace = _workspace_or_404(workspace_id)
    asset = _asset_or_404(workspace_id, asset_id)
    relative = request.args.get("path", "")
    root, current_folder, entries, browser_error = _safe_listing(asset, relative)
    return render_template(
        "assets.html",
        workspace=workspace,
        assets=AssetService(get_db()).list_assets(workspace_id),
        asset_types=ASSET_TYPES,
        selected_asset=asset,
        entries=entries,
        current_relative=relative,
        browser_error=browser_error,
        root=root,
        current_folder=current_folder,
    )
