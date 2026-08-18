"""Customer.name pasa a obligatorio (NOT NULL) y se rellenan las filas vacías.

Escrita a mano y no con makemigrations a propósito: en un cambio
null=True -> null=False, makemigrations pide un default de una sola vez por
consola y hornea `preserve_default=False` en el archivo. Aquí el relleno no
es un default, es una migración de datos.

Backfill y AlterField van en el MISMO archivo porque Django envuelve cada
migración en una transacción (atomic=True) en Postgres: así el relleno y la
constraint caen juntos o no cae ninguno. Separados en dos archivos serían dos
transacciones, y existiría un estado intermedio en el que alguien puede
quedarse (migrate files 0003 y parar) o un rollback que deja los nombres de
relleno sin la constraint que los justificaba.
"""

from django.db import migrations, models
from django.db.models import Q

# Marcador deliberadamente evidente y CONSULTABLE, para que quien limpie estos
# datos pueda listarlos:
#     Customer.objects.filter(name__startswith="Sin nombre (#")
# Se descartaron "" (deja el <option> ilegible, que es justo el problema que
# esta migración arregla) y "Sin Nombre" (colapsa N clientes distintos en N
# etiquetas idénticas dentro del mismo <select>: peor que el estado actual
# para quien intenta elegir el correcto).
PLACEHOLDER = "Sin nombre (#{pk})"


def backfill_customer_names(apps, schema_editor):
    Customer = apps.get_model("files", "Customer")
    # Las DOS mitades del filtro hacen falta, no solo isnull: solo NULL impide
    # que el AlterField funcione, pero Django no convierte "" a None al
    # guardar, así que las filas creadas con nombre en blanco desde
    # CustomerCreateView o el admin están guardadas como "" y sobrevivirían al
    # ALTER en silencio, incoherentes con el nuevo invariante.
    filas = list(
        Customer.objects.using(schema_editor.connection.alias)
        .filter(Q(name__isnull=True) | Q(name=""))
    )
    for fila in filas:
        fila.name = PLACEHOLDER.format(pk=fila.pk)
    if filas:
        Customer.objects.using(schema_editor.connection.alias).bulk_update(filas, ["name"])


class Migration(migrations.Migration):

    dependencies = [
        ("files", "0002_remove_filearchive_blob_path_and_more"),
    ]

    operations = [
        # Sin reverse: al revertir, el AlterField devuelve null=True, y
        # deshacer el relleno exigiría adivinar qué nombres eran marcadores y
        # cuáles reales. Tampoco se marca elidable: un squash que se cargue el
        # relleno antes de un ALTER a NOT NULL es un squash roto.
        migrations.RunPython(backfill_customer_names, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="customer",
            name="name",
            field=models.CharField(max_length=100, verbose_name="Nombre"),
        ),
    ]
