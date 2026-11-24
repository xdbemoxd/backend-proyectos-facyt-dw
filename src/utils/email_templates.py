"""Plantillas de correo: (asunto, texto plano, html)."""
from html import escape

from src.utils.mailer import APP_URL

ROLE_LABELS = {
    "admin_sistema": "Administrador del sistema",
    "aprobador": "Aprobador / Gestor de inventario",
    "coordinador": "Coordinador de proyecto",
}


def role_label(name: str | None, description: str | None = None) -> str:
    return ROLE_LABELS.get(name or "", description or name or "Sin rol")


def _layout(title: str, body_html: str) -> str:
    return f"""<!doctype html>
<html lang="es"><body style="margin:0;background:#f4f4f5;font-family:Arial,Helvetica,sans-serif;color:#18181b">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:24px 12px">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="max-width:560px;background:#ffffff;border:1px solid #d4d4d8;border-radius:12px">
        <tr><td style="padding:24px 28px;border-bottom:1px solid #e4e4e7">
          <strong style="font-size:16px">Control de Proyectos FaCyT</strong>
        </td></tr>
        <tr><td style="padding:28px">
          <h1 style="margin:0 0 16px;font-size:20px">{escape(title)}</h1>
          {body_html}
        </td></tr>
        <tr><td style="padding:16px 28px;border-top:1px solid #e4e4e7;font-size:12px;color:#52525b">
          Correo automático. No respondas a este mensaje.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def invitation_email(email: str, role: str) -> tuple[str, str, str]:
    login_url = f"{APP_URL}/login"
    subject = "Te dieron acceso a Control de Proyectos FaCyT"
    text = (
        f"Hola,\n\n"
        f"Se creó una cuenta para {email} en Control de Proyectos FaCyT con el rol: {role}.\n\n"
        f"Para entrar, abre {login_url} y pulsa \"Iniciar sesión con Google\" usando este mismo correo. "
        f"Tu nombre se tomará de tu cuenta de Google en el primer ingreso.\n"
    )
    body = f"""
      <p style="margin:0 0 12px;line-height:1.5">Hola,</p>
      <p style="margin:0 0 12px;line-height:1.5">
        Se creó una cuenta para <strong>{escape(email)}</strong> con el rol
        <strong>{escape(role)}</strong>.
      </p>
      <p style="margin:0 0 24px;line-height:1.5">
        Para entrar, inicia sesión con Google usando este mismo correo.
        Tu nombre se tomará de tu cuenta de Google en el primer ingreso.
      </p>
      <a href="{escape(login_url)}"
         style="display:inline-block;background:#18181b;color:#ffffff;text-decoration:none;
                padding:12px 20px;border-radius:8px;font-weight:bold">Iniciar sesión</a>
      <p style="margin:24px 0 0;font-size:12px;color:#52525b">
        Si el botón no funciona, copia este enlace: {escape(login_url)}
      </p>"""
    return subject, text, _layout("Bienvenido al sistema", body)


def admin_notice_email(new_email: str, role: str, created_by: str) -> tuple[str, str, str]:
    users_url = f"{APP_URL}/dashboard/users"
    subject = f"Nuevo usuario registrado: {new_email}"
    text = (
        f"Se registró un nuevo usuario en Control de Proyectos FaCyT.\n\n"
        f"Correo: {new_email}\nRol: {role}\nRegistrado por: {created_by}\n\n"
        f"Ver usuarios: {users_url}\n"
    )
    row = 'style="padding:8px 0;border-bottom:1px solid #e4e4e7"'
    body = f"""
      <p style="margin:0 0 16px;line-height:1.5">Se registró un nuevo usuario en el sistema.</p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:14px;margin-bottom:24px">
        <tr><td {row}><span style="color:#52525b">Correo</span></td><td {row}><strong>{escape(new_email)}</strong></td></tr>
        <tr><td {row}><span style="color:#52525b">Rol</span></td><td {row}>{escape(role)}</td></tr>
        <tr><td {row}><span style="color:#52525b">Registrado por</span></td><td {row}>{escape(created_by)}</td></tr>
      </table>
      <a href="{escape(users_url)}"
         style="display:inline-block;background:#18181b;color:#ffffff;text-decoration:none;
                padding:12px 20px;border-radius:8px;font-weight:bold">Ver usuarios</a>"""
    return subject, text, _layout("Nuevo usuario", body)
