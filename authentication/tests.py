"""4 grupos de rol (authentication/signals.py): Admin, Colaborador, Estandar,
Basico.

Un catálogo nuevo cuya única vía de alta es un modal necesita `add` en
Estandar/Colaborador/Admin, o esos roles reciben un 403 del endpoint de alta
rápida y se quedan con un <select> que no pueden rellenar desde ninguna
parte. Es un fallo que no aparece en ningún test de `files` (allí los
permisos se conceden a mano, uno por uno), así que se fija aquí.
"""

from django.contrib.auth.models import Group, User
from django.test import TestCase

from .signals import (
    DEFAULT_ROLE_GROUP_NAME,
    LEGACY_GROUP_NAME,
    MANAGED_MODELS,
    ROLE_GROUPS,
    ROLE_PERMISSION_PREFIXES,
    ensure_role_groups,
    migrate_legacy_group_members,
)


def _expected_codenames(prefixes):
    return {f"{prefix}_{model}" for _, model in MANAGED_MODELS for prefix in prefixes}


class EnsureRoleGroupsTests(TestCase):

    def test_creates_all_four_groups(self):
        groups = ensure_role_groups()
        self.assertIsNotNone(groups)
        self.assertEqual(set(groups.keys()), set(ROLE_GROUPS))
        self.assertEqual(Group.objects.filter(name__in=ROLE_GROUPS).count(), 4)

    def test_admin_and_colaborador_get_view_add_change(self):
        groups = ensure_role_groups()
        esperados = _expected_codenames(("view", "add", "change"))
        for name in ("Admin", "Colaborador"):
            codenames = set(groups[name].permissions.values_list("codename", flat=True))
            self.assertEqual(codenames, esperados, name)

    def test_estandar_gets_view_and_add_only(self):
        groups = ensure_role_groups()
        codenames = set(groups["Estandar"].permissions.values_list("codename", flat=True))
        self.assertEqual(codenames, _expected_codenames(("view", "add")))

    def test_basico_gets_view_only(self):
        groups = ensure_role_groups()
        codenames = set(groups["Basico"].permissions.values_list("codename", flat=True))
        self.assertEqual(codenames, _expected_codenames(("view",)))

    def test_never_grants_delete_to_any_role_group(self):
        groups = ensure_role_groups()
        for name, group in groups.items():
            codenames = group.permissions.values_list("codename", flat=True)
            self.assertFalse([c for c in codenames if c.startswith("delete_")], name)

    def test_permission_matrix_never_declares_delete(self):
        for name, prefixes in ROLE_PERMISSION_PREFIXES.items():
            self.assertNotIn("delete", prefixes, name)

    def test_is_idempotent(self):
        primero = ensure_role_groups()
        segundo = ensure_role_groups()
        for name in ROLE_GROUPS:
            self.assertEqual(primero[name].pk, segundo[name].pk)
        self.assertEqual(Group.objects.filter(name__in=ROLE_GROUPS).count(), 4)

    def test_grants_add_personactivitytype_to_estandar(self):
        # Explícito y aparte del test genérico: es el permiso sin el que el
        # modal "+ Nuevo" del campo "Tipo de persona" devuelve 403.
        groups = ensure_role_groups()
        self.assertTrue(groups["Estandar"].permissions.filter(codename="add_personactivitytype").exists())


class DefaultGroupAssignmentTests(TestCase):

    def test_new_non_superuser_lands_in_estandar(self):
        user = User.objects.create_user("nueva", password="x")
        self.assertTrue(user.groups.filter(name=DEFAULT_ROLE_GROUP_NAME).exists())
        self.assertTrue(user.has_perm("files.add_personactivitytype"))

    def test_new_user_not_added_to_admin_or_colaborador(self):
        user = User.objects.create_user("nueva2", password="x")
        self.assertFalse(user.groups.filter(name__in=("Admin", "Colaborador")).exists())

    def test_superuser_is_not_added_to_any_group(self):
        user = User.objects.create_superuser("jefa", "jefa@example.com", "x")
        self.assertFalse(user.groups.filter(name__in=ROLE_GROUPS).exists())


class LegacyGroupMigrationTests(TestCase):

    def test_migrates_members_of_legacy_group_into_estandar(self):
        legacy = Group.objects.create(name=LEGACY_GROUP_NAME)
        user = User.objects.create_user("vieja", password="x")
        user.groups.clear()
        user.groups.add(legacy)

        result = migrate_legacy_group_members()

        self.assertIn(user, result["migrated_users"])
        self.assertTrue(result["deleted_legacy_group"])
        user.refresh_from_db()
        self.assertTrue(user.groups.filter(name="Estandar").exists())
        self.assertFalse(Group.objects.filter(name=LEGACY_GROUP_NAME).exists())

    def test_no_legacy_group_is_a_harmless_noop(self):
        result = migrate_legacy_group_members()
        self.assertEqual(result["migrated_users"], [])
        self.assertFalse(result["deleted_legacy_group"])

    def test_user_without_any_role_group_is_migrated_to_estandar(self):
        user = User.objects.create_user("huerfana", password="x")
        user.groups.clear()

        result = migrate_legacy_group_members()

        self.assertIn(user, result["migrated_users"])
        user.refresh_from_db()
        self.assertTrue(user.groups.filter(name="Estandar").exists())

    def test_superuser_is_never_migrated(self):
        user = User.objects.create_superuser("jefa2", "jefa2@example.com", "x")

        result = migrate_legacy_group_members()

        self.assertNotIn(user, result["migrated_users"])
        self.assertFalse(user.groups.exists())


class MeEndpointTests(TestCase):

    def test_requires_login(self):
        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, 302)

    def test_reports_estandar_role_and_matching_permissions(self):
        user = User.objects.create_user("estandar", password="x")
        self.client.force_login(user)

        response = self.client.get("/api/me/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["role"], "Estandar")
        self.assertFalse(data["is_superuser"])
        self.assertEqual(
            data["permissions"]["customer"],
            {"view": True, "add": True, "change": False},
        )
        self.assertEqual(
            data["permissions"]["filearchive"],
            {"view": True, "add": True, "change": False},
        )

    def test_reports_highest_priority_role_when_user_has_multiple_groups(self):
        user = User.objects.create_user("multi", password="x")
        groups = ensure_role_groups()
        user.groups.add(groups["Admin"])
        self.client.force_login(user)

        response = self.client.get("/api/me/")

        self.assertEqual(response.json()["role"], "Admin")

    def test_superuser_has_null_role_and_all_permissions_true(self):
        user = User.objects.create_superuser("jefa3", "jefa3@example.com", "x")
        self.client.force_login(user)

        response = self.client.get("/api/me/")

        data = response.json()
        self.assertIsNone(data["role"])
        self.assertTrue(data["is_superuser"])
        for model_perms in data["permissions"].values():
            self.assertTrue(all(model_perms.values()))
