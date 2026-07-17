"""Local user identity routes for the alpha workspace."""

from __future__ import annotations

from flask import Blueprint, g, redirect, render_template, request, session, url_for

from agent_workbench.services.identity_service import IdentityService
from agent_workbench.web.app import get_db

bp = Blueprint("identity", __name__, url_prefix="/identity")


@bp.route("", methods=["GET", "POST"])
def edit_identity():
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        if display_name:
            user = IdentityService(get_db()).update_display_name(
                g.current_user.user_id, display_name
            )
            g.current_user = user
            session["workbench_user_id"] = user.user_id
        next_url = request.form.get("next") or url_for("channels.index")
        return redirect(next_url)
    return render_template(
        "identity.html",
        current_user=g.current_user,
        next_url=request.args.get("next", ""),
    )
