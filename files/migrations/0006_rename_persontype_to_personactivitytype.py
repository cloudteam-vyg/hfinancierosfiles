"""Renombra PersonType -> PersonActivityType y Customer.person_type ->
Customer.tipo_persona_actividad.

Hand-written a propósito (no generada dejando que makemigrations adivine el
rename): en el mismo cambio de modelos también desaparecen ClassName y
ActivityType, que tienen la MISMA forma de campos (name/description) que el
viejo PersonType, así que el detector de renombres de Django puede emparejar
mal ("¿renombraste classname a personactivitytype?"). Esta migración hace
ÚNICAMENTE el rename -- ver la 0007 para el resto.

RenameModel/RenameField son operaciones reales de ALTER TABLE/ALTER COLUMN
RENAME: cada valor de person_type ya asignado a un cliente se conserva tal
cual, sin backfill. RenameModel además hace que Django reescriba en su sitio
el ContentType.model existente (contrib.contenttypes conecta esto a
pre_migrate) en vez de borrar y crear ContentType/Permission nuevos, así que
los Group.permissions que ya tenían add_persontype/change_persontype/etc. los
conservan bajo los codenames *_personactivitytype sin tocar
authentication/signals.py en tiempo de migración.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('files', '0005_persontype_customer_notes_archive_contact'),
    ]

    operations = [
        migrations.RenameModel(old_name='PersonType', new_name='PersonActivityType'),
        migrations.RenameField(model_name='customer', old_name='person_type', new_name='tipo_persona_actividad'),
    ]
