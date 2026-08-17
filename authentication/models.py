"""Esta app no define modelos: usa el User de django.contrib.auth tal cual.

Su responsabilidad es el acceso (login/logout vía las vistas nativas de
Django, ver urls.py) y el alta automática de permisos por grupo
(ver signals.py::ensure_standard_group).

El módulo se conserva vacío a propósito: es el punto de anclaje si algún
día se necesita un modelo de usuario propio (AUTH_USER_MODEL).
"""
