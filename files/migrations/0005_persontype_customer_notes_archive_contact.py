"""Catálogo PersonType, Customer.person_type/notes y FileArchive.contact.

Generada con makemigrations y revisada a mano. Dos cosas que conviene saber al
leerla:

- `person_type` y `notes` entran NULABLES, así que no hay backfill: un cliente
  sin tipo de persona es un estado válido (a diferencia de Customer.name, ver
  la migración 0003).
- `contact` sí es NOT NULL, y el `default=""` + `preserve_default=False` es
  exactamente lo que hornea makemigrations al responder a su pregunta: es un
  default de UNA SOLA VEZ para poblar las filas que ya existen, y el modelo se
  queda sin default, que es lo que hace que el campo sea obligatorio de aquí en
  adelante. Esas filas antiguas quedan con "" y hay que rellenarlas al
  editarlas; no se inventa un valor porque el contacto de un trámite pasado no
  es deducible desde aquí.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('files', '0004_catalog_verbose_names_and_ordering'),
    ]

    operations = [
        migrations.CreateModel(
            name='PersonType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, verbose_name='Nombre')),
                ('description', models.TextField(blank=True, null=True, verbose_name='Descripción')),
            ],
            options={
                'verbose_name': 'Tipo de persona',
                'verbose_name_plural': 'Tipos de persona',
                'ordering': ('name',),
            },
        ),
        migrations.AddField(
            model_name='customer',
            name='notes',
            field=models.CharField(blank=True, max_length=300, null=True, verbose_name='Notas'),
        ),
        migrations.AddField(
            model_name='filearchive',
            name='contact',
            field=models.CharField(default='', max_length=50, verbose_name='Contacto'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='historicalfilearchive',
            name='contact',
            field=models.CharField(default='', max_length=50, verbose_name='Contacto'),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='customer',
            name='date_of_constitution',
            field=models.DateField(verbose_name='Fecha de constitución/Nacimiento'),
        ),
        migrations.AddField(
            model_name='customer',
            name='person_type',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='customers', to='files.persontype', verbose_name='Tipo de persona'),
        ),
    ]
