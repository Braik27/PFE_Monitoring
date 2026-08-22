from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash
from storage import get_storage
from api.auth import require_admin, _precompute_stats

admin_bp = Blueprint("admin", __name__)

@admin_bp.get("/api/admin/users")
@require_admin
def list_users():
    """Liste tous les utilisateurs avec leurs stats."""
    users = get_storage().list_users()
    analyses_by_analyst, alert_status_by_token = _precompute_stats()
    for user in users:
        username = user.get("username", "")
        user["n_analyses"] = analyses_by_analyst.get(username, 0)
        user["n_pending_alerts"] = sum(1 for s in alert_status_by_token.values() if s in ("PENDING", "ACKNOWLEDGED"))
        user["n_resolved"] = sum(1 for s in alert_status_by_token.values() if s == "RESOLVED")
    return jsonify(users)

@admin_bp.post("/api/admin/users")
@require_admin
def create_user():
    """Crée un nouvel utilisateur."""
    data = request.get_json(silent=True) or {}
    username = data.get("username","").strip()
    password = data.get("password","")
    role = data.get("role","consultant")
    email = data.get("email","").strip()
    full_name = data.get("full_name","").strip()
    if not username or not password:
        return jsonify({"error": "Nom d'utilisateur et mot de passe requis"}), 400
    if len(password) < 6:
        return jsonify({"error": "Le mot de passe doit contenir au moins 6 caractères"}), 400
    existing = get_storage().get_user(username)
    if existing:
        return jsonify({"error": "Ce nom d'utilisateur existe déjà"}), 400
    user_id = get_storage().save_user(username, generate_password_hash(password), role)
    get_storage().update_user_profile(user_id, full_name=full_name, email=email)
    return jsonify({"ok": True, "id": user_id}), 201

@admin_bp.put("/api/admin/users/<int:user_id>")
@require_admin
def update_user(user_id: int):
    """Modifie un utilisateur (rôle, nom, email, etc.)."""
    data = request.get_json(silent=True) or {}
    updates = {}
    if "role" in data: updates["role"] = data["role"]
    if "full_name" in data: updates["full_name"] = data["full_name"]
    if "email" in data: updates["email"] = data["email"]
    if "active" in data: updates["active"] = 1 if data["active"] else 0
    if updates:
        get_storage().update_user(user_id, **updates)
    return jsonify({"ok": True})

@admin_bp.put("/api/admin/users/<int:user_id>/password")
@require_admin
def reset_password(user_id: int):
    """✅ Réinitialise le mot de passe d'un utilisateur (accessible depuis l'UI admin)."""
    data = request.get_json(silent=True) or {}
    new_password = data.get("password","")
    if not new_password or len(new_password) < 6:
        return jsonify({"error": "Le mot de passe doit contenir au moins 6 caractères"}), 400
    get_storage().update_user_password(user_id, generate_password_hash(new_password))
    return jsonify({"ok": True})

@admin_bp.put("/api/admin/users/<int:user_id>/toggle-active")
@require_admin
def toggle_user_active(user_id: int):
    """Active ou désactive un utilisateur."""
    data = request.get_json(silent=True) or {}
    active = data.get("active", True)
    get_storage().update_user_status(user_id, 1 if active else 0)
    return jsonify({"ok": True})

@admin_bp.delete("/api/admin/users/<int:user_id>")
@require_admin
def delete_user(user_id: int):
    """Supprime un utilisateur."""
    from flask import session
    current_user_id = session.get("user", {}).get("id")
    if user_id == current_user_id:
        return jsonify({"error": "Vous ne pouvez pas supprimer votre propre compte"}), 400
    get_storage().delete_user(user_id)
    return jsonify({"ok": True})

@admin_bp.get("/api/admin/stats")
@require_admin
def admin_stats():
    """Statistiques globales pour le dashboard admin."""
    analyses_by_analyst, alert_status_by_token = _precompute_stats()
    users = get_storage().list_users()
    return jsonify({
        "total_analyses": sum(analyses_by_analyst.values()),
        "total_alerts": len(alert_status_by_token),
        "pending_alerts": sum(1 for s in alert_status_by_token.values() if s == "PENDING"),
        "total_users": len(users),
        "active_users": sum(1 for u in users if u.get("active", 1) == 1),
    })