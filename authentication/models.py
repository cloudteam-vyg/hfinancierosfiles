"""Esta app no define modelos: usa el User de django.contrib.auth tal cual.

Su responsabilidad es el acceso (login/logout vía las vistas nativas de
Django, ver urls.py) y el alta automática de los 4 grupos de rol con sus
permisos (ver signals.py::ensure_role_groups, gestionable con
`manage.py setup_groups`).

El módulo se conserva vacío a propósito: es el punto de anclaje si algún
día se necesita un modelo de usuario propio (AUTH_USER_MODEL).
"""
