import os
import unicodedata

from dotenv import load_dotenv

load_dotenv()

# Nombre del rol administrador tal como esta en la BD (private.rol.name).
ADMIN_ROLE = "Administrador del Sistema"

# Roles que pueden usar el sistema: nombres EXACTOS de private.rol.name.
# Un usuario con otro rol recibe 403 en todas las rutas protegidas.
# Configurable: ALLOWED_ROLES="Administrador del Sistema,Aprobador,Coordinador"
_DEFAULT_ROLES = [ ADMIN_ROLE, "Aprobador", "Coordinador" ]
ALLOWED_ROLES: frozenset[str] = frozenset(
    r.strip()
    for r in ( os.getenv( "ALLOWED_ROLES" ) or ",".join( _DEFAULT_ROLES ) ).split( "," )
    if r.strip()
) | { ADMIN_ROLE }


def normalize_role( name: str | None ) -> str:
    # "Administrador del Sistema" -> "administrador del sistema" (sin tildes)
    if not name:
        return ""
    r = unicodedata.normalize( "NFD", name )
    return "".join( c for c in r if unicodedata.category( c ) != "Mn" ).lower().strip()


def role_matches( rol_name: str | None, *allowed: str ) -> bool:
    current = normalize_role( rol_name )
    return bool( current ) and any( current == normalize_role( a ) for a in allowed )


def is_admin_role( rol_name: str | None ) -> bool:
    # Acepta el nombre exacto de la BD y el codigo "admin_sistema".
    return role_matches( rol_name, ADMIN_ROLE, "admin_sistema" )
