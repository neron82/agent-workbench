"""Settings blueprint for providers, roles, agent profiles, and tools."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from agent_workbench.models.tool import ToolRepository
from agent_workbench.services.profile_service import ProfileNotFoundError, ProfileService
from agent_workbench.services.provider_service import (
    ProviderInUseError,
    ProviderNotFoundError,
    ProviderService,
)
from agent_workbench.services.role_service import (
    RoleInUseError,
    RoleNotFoundError,
    RoleService,
)
from agent_workbench.services.secret_store import (
    delete_secret,
    describe_secret_source,
    get_secrets_file,
    is_valid_env_var_name,
    normalize_env_var_name,
    save_secret,
)
from agent_workbench.web.app import get_db

# ── Provider presets ──────────────────────────────────────────────────
PROVIDER_PRESETS: Dict[str, Dict[str, str]] = {
    "openai": {
        "name": "OpenAI",
        "endpoint_url": "https://api.openai.com/v1",
        "provider_kind": "openai_compatible",
    },
    "ollama_cloud": {
        "name": "Ollama Cloud",
        "endpoint_url": "https://ollama.com/v1",
        "provider_kind": "openai_compatible",
    },
}

bp = Blueprint("settings", __name__, url_prefix="/settings")


def _parse_json_field(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        abort(400, description=f"Invalid JSON in form field: {exc.msg}")


def _provider_service() -> ProviderService:
    return ProviderService(get_db())


def _role_service() -> RoleService:
    return RoleService(get_db())


def _profile_service() -> ProfileService:
    return ProfileService(get_db())


def _latest_profiles_by_name() -> Sequence[object]:
    return ProfileService(get_db()).list_latest_profiles()


def _normalize_provider_secret_form(name: str, env_name_raw: str, key_value: str) -> Optional[str]:
    env_name = (env_name_raw or "").strip()
    key_value = key_value or ""
    if key_value and not env_name:
        env_name = normalize_env_var_name(name)
    if env_name and not is_valid_env_var_name(env_name):
        abort(400, description=f"Invalid env var name: {env_name!r}")
    return env_name or None


def _provider_secret_status_map(providers) -> Dict[str, str]:
    return {
        provider.provider_id: describe_secret_source(provider.api_key_env_var)
        for provider in providers
    }


@bp.route("")
def index():
    return redirect(url_for("settings.providers_page"))


@bp.route("/providers", methods=["GET"])
def providers_page():
    providers = _provider_service().list_providers()
    return render_template(
        "settings/providers.html",
        providers=providers,
        provider_secret_status=_provider_secret_status_map(providers),
        secrets_file=str(get_secrets_file()),
        presets=PROVIDER_PRESETS,
    )


# ── API: test endpoint + fetch model list ─────────────────────────────


@bp.route("/providers/test-and-fetch-models", methods=["POST"])
def test_and_fetch_models():
    """Test an API key against an endpoint and return the model list.

    Expects JSON body::

        {
          "endpoint_url": "https://api.openai.com/v1",
          "api_key": "sk-...",
          "provider_kind": "openai_compatible"
        }

    Returns JSON::

        {"ok": true, "models": ["gpt-4o", ...]}
        {"ok": false, "error": "..."}
    """
    import urllib.error
    import urllib.request

    data = request.get_json(silent=True) or {}
    endpoint_url = (data.get("endpoint_url") or "").strip().rstrip("/")
    api_key = (data.get("api_key") or "").strip()
    provider_kind = (data.get("provider_kind") or "openai_compatible").strip()

    if not endpoint_url:
        return {"ok": False, "error": "No endpoint URL provided"}

    # Build the /v1/models URL
    models_url = endpoint_url.rstrip("/") + "/models"

    headers = {"Content-Type": "application/json"}
    if api_key and provider_kind != "mock":
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        req = urllib.request.Request(models_url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")[:500]
        return {"ok": False, "error": f"HTTP {exc.code}: {err_body}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": str(exc.reason)}
    except (json.JSONDecodeError, OSError) as exc:
        return {"ok": False, "error": str(exc)}

    # Parse model list — OpenAI returns {"data": [{"id": "..."}, ...]},
    # Ollama returns {"models": [{"name": "..."}, ...]}, some return flat list.
    models: List[str] = []
    if isinstance(body, dict):
        for key in ("data", "models", "model_ids"):
            raw = body.get(key)
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, str):
                        models.append(item)
                    elif isinstance(item, dict):
                        models.append(
                            item.get("id") or item.get("name") or item.get("model") or ""
                        )
                break
    elif isinstance(body, list):
        for item in body:
            if isinstance(item, str):
                models.append(item)
            elif isinstance(item, dict):
                models.append(item.get("id") or item.get("name") or "")

    models = [m for m in models if m]
    if not models:
        return {"ok": False, "error": "Endpoint responded but no models found in response"}

    return {"ok": True, "models": sorted(set(models))}


@bp.route("/providers", methods=["POST"])
def create_provider():
    service = _provider_service()
    name = (request.form.get("name") or "").strip()
    api_key_value = request.form.get("api_key_value") or ""
    api_key_env_var = _normalize_provider_secret_form(
        name,
        request.form.get("api_key_env_var") or "",
        api_key_value,
    )
    service.create_provider(
        name=name,
        provider_kind=(request.form.get("provider_kind") or "mock").strip(),
        endpoint_url=(request.form.get("endpoint_url") or "").strip() or None,
        api_key_env_var=api_key_env_var,
        default_model=(request.form.get("default_model") or "").strip() or None,
        config_json=_parse_json_field(request.form.get("config_json")),
        is_enabled=request.form.get("is_enabled") in ("1", "true", "on"),
    )
    if api_key_value and api_key_env_var:
        secret_path = save_secret(api_key_env_var, api_key_value)
        flash(
            f"Provider angelegt. API-Key lokal in {secret_path} gespeichert; kein Server-Restart nötig.",
            "success",
        )
    else:
        flash("Provider angelegt.", "success")
    return redirect(url_for("settings.providers_page"))


@bp.route("/providers/<provider_id>", methods=["POST"])
def update_provider(provider_id: str):
    service = _provider_service()
    try:
        existing = service.get_provider(provider_id)
        name = (request.form.get("name") or "").strip() or existing.name
        api_key_value = request.form.get("api_key_value") or ""
        api_key_env_var = _normalize_provider_secret_form(
            name,
            request.form.get("api_key_env_var") or (existing.api_key_env_var or ""),
            api_key_value,
        )
        service.update_provider(
            provider_id,
            name=name,
            provider_kind=(request.form.get("provider_kind") or "").strip() or None,
            endpoint_url=(request.form.get("endpoint_url") or "").strip() or None,
            api_key_env_var=api_key_env_var,
            default_model=(request.form.get("default_model") or "").strip() or None,
            config_json=_parse_json_field(request.form.get("config_json")),
            is_enabled=request.form.get("is_enabled") in ("1", "true", "on"),
        )
    except ProviderNotFoundError:
        abort(404, description=f"Provider {provider_id!r} not found")

    if request.form.get("clear_saved_api_key") in ("1", "true", "on") and api_key_env_var:
        delete_secret(api_key_env_var)
        flash("Gespeicherten API-Key entfernt.", "success")
    if api_key_value and api_key_env_var:
        secret_path = save_secret(api_key_env_var, api_key_value)
        flash(
            f"Provider aktualisiert. API-Key lokal in {secret_path} gespeichert; kein Server-Restart nötig.",
            "success",
        )
    else:
        flash("Provider aktualisiert.", "success")
    return redirect(url_for("settings.providers_page"))


@bp.route("/providers/<provider_id>/delete", methods=["POST"])
def delete_provider(provider_id: str):
    service = _provider_service()
    try:
        service.delete_provider(provider_id)
        flash("Provider gelöscht.", "success")
    except ProviderInUseError as exc:
        flash(str(exc), "error")
    except ProviderNotFoundError:
        abort(404, description=f"Provider {provider_id!r} not found")
    return redirect(url_for("settings.providers_page"))


@bp.route("/roles", methods=["GET"])
def roles_page():
    return render_template(
        "settings/roles.html",
        roles=_role_service().list_roles(),
    )


@bp.route("/roles", methods=["POST"])
def create_role():
    _role_service().create_role(
        name=(request.form.get("name") or "").strip(),
        description=(request.form.get("description") or "").strip(),
        system_prompt=(request.form.get("system_prompt") or "").strip(),
    )
    flash("Rolle angelegt.", "success")
    return redirect(url_for("settings.roles_page"))


@bp.route("/roles/<role_id>", methods=["POST"])
def update_role(role_id: str):
    try:
        _role_service().update_role(
            role_id,
            name=(request.form.get("name") or "").strip() or None,
            description=(request.form.get("description") or "").strip() or None,
            system_prompt=(request.form.get("system_prompt") or "").strip() or None,
        )
    except RoleNotFoundError:
        abort(404, description=f"Role {role_id!r} not found")
    flash("Rolle aktualisiert.", "success")
    return redirect(url_for("settings.roles_page"))


@bp.route("/roles/<role_id>/delete", methods=["POST"])
def delete_role(role_id: str):
    try:
        _role_service().delete_role(role_id)
        flash("Rolle gelöscht.", "success")
    except RoleInUseError as exc:
        flash(str(exc), "error")
    except RoleNotFoundError:
        abort(404, description=f"Role {role_id!r} not found")
    return redirect(url_for("settings.roles_page"))


@bp.route("/agents", methods=["GET"])
def agents_page():
    provider_service = _provider_service()
    role_service = _role_service()
    return render_template(
        "settings/agents.html",
        agents=_latest_profiles_by_name(),
        providers=provider_service.list_providers(),
        roles=role_service.list_roles(),
        harness_options=["hermes", "discussion", "opencode", "shell", "ssh"],
    )


@bp.route("/agents", methods=["POST"])
def create_agent():
    provider_ref = (request.form.get("provider_ref") or "").strip()
    function_ref = (request.form.get("function_ref") or "").strip()
    if not provider_ref or not function_ref:
        flash("Provider und Rolle sind für Agenten Pflicht.", "error")
        return redirect(url_for("settings.agents_page"))
    _profile_service().create_profile(
        name=(request.form.get("name") or "").strip(),
        provider=provider_ref,
        model=(request.form.get("model_ref") or "").strip() or None,
        perspective=(request.form.get("perspective_ref") or "").strip() or None,
        function=function_ref,
        harness=(request.form.get("harness_ref") or "").strip() or None,
        capability_hints=_parse_json_field(request.form.get("capability_hints_json")),
    )
    flash("Agent angelegt.", "success")
    return redirect(url_for("settings.agents_page"))


@bp.route("/agents/<agent_profile_id>", methods=["POST"])
def update_agent(agent_profile_id: str):
    provider_ref = (request.form.get("provider_ref") or "").strip()
    function_ref = (request.form.get("function_ref") or "").strip()
    if not provider_ref or not function_ref:
        flash("Provider und Rolle sind für Agenten Pflicht.", "error")
        return redirect(url_for("settings.agents_page"))
    try:
        updated = _profile_service().update_profile(
            agent_profile_id,
            name=(request.form.get("name") or "").strip() or None,
            provider=provider_ref,
            model=(request.form.get("model_ref") or "").strip() or None,
            perspective=(request.form.get("perspective_ref") or "").strip() or None,
            function=function_ref,
            harness=(request.form.get("harness_ref") or "").strip() or None,
            capability_hints=_parse_json_field(request.form.get("capability_hints_json")),
        )
    except ProfileNotFoundError:
        abort(404, description=f"Agent profile {agent_profile_id!r} not found")
    flash(f"Agent aktualisiert (neue Version: {updated.version}).", "success")
    return redirect(url_for("settings.agents_page"))


@bp.route("/agents/<agent_profile_id>/delete", methods=["POST"])
def delete_agent(agent_profile_id: str):
    profile_service = _profile_service()
    profile = profile_service.get_profile(agent_profile_id)
    # Count only *active* participants (not soft-deleted ones).
    active = get_db().execute(
        "SELECT COUNT(*) AS n FROM session_participants sp "
        "JOIN agent_profile_bindings apb ON apb.binding_id = sp.binding_id "
        "WHERE apb.agent_profile_id = ? AND sp.removed_at IS NULL",
        (agent_profile_id,),
    ).fetchone()["n"]
    if active:
        flash(
            f"Agent {profile.name!r} kann nicht gelöscht werden: "
            f"{active} aktive Teilnehmer binden an dieses Profil.",
            "error",
        )
        return redirect(url_for("settings.agents_page"))
    # Remove orphan bindings so the profile can be deleted cleanly.
    get_db().execute(
        "DELETE FROM agent_profile_bindings WHERE agent_profile_id = ?",
        (agent_profile_id,),
    )
    get_db().commit()
    deleted = profile_service.profiles.delete(agent_profile_id)
    if not deleted:
        abort(404, description=f"Agent profile {agent_profile_id!r} not found")
    flash("Agent gelöscht.", "success")
    return redirect(url_for("settings.agents_page"))


# -----------------------------------------------------------------------
# Tools
# -----------------------------------------------------------------------


@bp.route("/tools", methods=["GET"])
def tools_page():
    """List the tool catalog (builtin + custom, enabled and disabled)."""
    repo = ToolRepository(get_db())
    tools = repo.list_all()
    return render_template("settings/tools.html", tools=tools)


@bp.route("/tools/<tool_id>/toggle", methods=["POST"])
def tool_toggle(tool_id: str):
    """Toggle a tool's enabled flag."""
    repo = ToolRepository(get_db())
    tool = repo.get_by_id(tool_id)
    if tool is None:
        abort(404, description=f"Tool {tool_id!r} not found")
    repo.update(tool_id, is_enabled=not tool.is_enabled)
    flash(
        f"Tool {tool.name!r} ist jetzt {'aktiv' if not tool.is_enabled else 'inaktiv'}.",
        "success",
    )
    return redirect(url_for("settings.tools_page"))
