"""Etiquetas en español y orden por defecto para los tres catálogos.

Separada de 0003 a propósito y no fusionada con ella: 0003 lleva la única
operación con riesgo real (un ALTER ... SET NOT NULL con relleno de datos), y
si alguna vez hay que revertirla en producción no se quiere arrastrar de vuelta
las etiquetas y el orden.

Todo aquí es state-only -- `verbose_name` y `Meta.ordering` no son parámetros
de esquema, así que `sqlmigrate files 0004` no muestra SQL.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("files", "0003_customer_name_required"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="activitytype",
            options={
                "ordering": ("name",),
                "verbose_name": "Tipo de actividad",
                "verbose_name_plural": "Tipos de actividad",
            },
        ),
        migrations.AlterModelOptions(
            name="archiveclass",
            options={
                "ordering": ("name",),
                "verbose_name": "Clase de archivo",
                "verbose_name_plural": "Clases de archivo",
            },
        ),
        migrations.AlterModelOptions(
            name="classname",
            options={
                "ordering": ("name",),
                "verbose_name": "Clase de cliente",
                "verbose_name_plural": "Clases de cliente",
            },
        ),
        migrations.AlterField(
            model_name="activitytype",
            name="description",
            field=models.TextField(blank=True, null=True, verbose_name="Descripción"),
        ),
        migrations.AlterField(
            model_name="activitytype",
            name="name",
            field=models.CharField(max_length=100, verbose_name="Nombre"),
        ),
        migrations.AlterField(
            model_name="archiveclass",
            name="description",
            field=models.TextField(blank=True, null=True, verbose_name="Descripción"),
        ),
        migrations.AlterField(
            model_name="archiveclass",
            name="name",
            field=models.CharField(max_length=100, verbose_name="Nombre"),
        ),
        migrations.AlterField(
            model_name="classname",
            name="description",
            field=models.TextField(blank=True, null=True, verbose_name="Descripción"),
        ),
        migrations.AlterField(
            model_name="classname",
            name="name",
            field=models.CharField(max_length=100, verbose_name="Nombre"),
        ),
    ]
