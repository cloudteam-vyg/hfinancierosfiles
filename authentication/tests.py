"""Grupo "Usuarios estándar" (authentication/signals.py).

Un catálogo nuevo cuya única vía de alta es un modal necesita `add` en este
grupo, o el usuario estándar recibe un 403 del endpoint de alta rápida y se
queda con un <select> que no puede rellenar desde ninguna parte. Es un fallo que
no aparece en ningún test de `files` (allí los permisos se conceden a mano, uno
por uno), así que se fija aquí.
"""

from django.contrib.auth.models import Group, User
from django.test import TestCase

from .signals import MANAGED_MODELS, STANDARD_GROUP_NAME, ensure_standard_group


class EnsureStandardGroupTests(TestCase):

    def test_grants_add_and_change_on_every_managed_model(self):
        group = ensure_standard_group()
        self.assertIsNotNone(group)
        codenames = set(group.permissions.values_list("codename", flat=True))
        esperados = {
            f"{prefix}_{model}"
            for _, model in MANAGED_MODELS
            for prefix in ("add", "change")
        }
        self.assertEqual(codenames, esperados)

    def test_grants_add_persontype(self):
        # Explícito y aparte del test genérico: es el permiso sin el que el
        # modal "+ Nuevo" del campo "Tipo de persona" devuelve 403.
        group = ensure_standard_group()
        self.assertTrue(group.permissions.filter(codename="add_persontype").exists())

    def test_never_grants_delete_or_view(self):
        group = ensure_standard_group()
        codenames = group.permissions.values_list("codename", flat=True)
        self.assertFalse([c for c in codenames if c.startswith(("delete_", "view_"))])

    def test_is_idempotent(self):
        primero = ensure_standard_group()
        segundo = ensure_standard_group()
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(Group.objects.filter(name=STANDARD_GROUP_NAME).count(), 1)

    def test_new_non_superuser_lands_in_the_group(self):
        user = User.objects.create_user("estandar", password="x")
        self.assertTrue(user.groups.filter(name=STANDARD_GROUP_NAME).exists())
        self.assertTrue(user.has_perm("files.add_persontype"))

    def test_superuser_is_not_added_to_the_group(self):
        user = User.objects.create_superuser("jefa", "jefa@example.com", "x")
        self.assertFalse(user.groups.filter(name=STANDARD_GROUP_NAME).exists())
