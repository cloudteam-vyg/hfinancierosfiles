"""Agrega Customer.contacto y elimina los catálogos ClassName/ActivityType.

Hand-written, segunda mitad del cambio que empezó en la migración 0006 (ver su
docstring). `contacto` es CharField(max_length=100), opcional (blank/null),
igual que el resto de campos de texto de Customer (group, phone_number,
country, notes) -- no exige backfill.

Eliminar `Customer.classname`/`ClassName` y `Customer.activity_type`/
`ActivityType` es DESTRUCTIVO a propósito: el dato de "Clase de cliente" y de
"Tipo de actividad" de cada cliente existente se pierde, no se conserva en
ningún otro campo. Confirmado como parte del alcance de este cambio (solo
sobrevive el dato de "Tipo de persona", renombrado en la 0006 a
tipo_persona_actividad).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('files', '0006_rename_persontype_to_personactivitytype'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='contacto',
            field=models.CharField(blank=True, max_length=100, null=True, verbose_name='Contacto'),
        ),
        migrations.RemoveField(
            model_name='customer',
            name='activity_type',
        ),
        migrations.DeleteModel(
            name='ActivityType',
        ),
        migrations.RemoveField(
            model_name='customer',
            name='classname',
        ),
        migrations.DeleteModel(
            name='ClassName',
        ),
    ]
