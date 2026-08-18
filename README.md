# HFinancieros Files

Backoffice Django para la gestión documental de clientes: subida de archivos
de hasta 300 MB, previsualización en el navegador y control de acceso por
permisos. La subida es síncrona: el archivo queda disponible en cuanto
termina de subirse, sin estados intermedios ni colas.

Los archivos se guardan en **disco local** (un volumen persistente de Dokku en
producción), nunca en un bucket externo, y se sirven siempre a través de vistas
autenticadas de Django — `MEDIA_URL` no se expone públicamente.

---

## Pila tecnológica

| Capa | Tecnología |
|---|---|
| Backend | Django 4.2 (Python 3.11) |
| Base de datos | PostgreSQL (vía `DATABASE_URL`) |
| Servidor | Gunicorn con workers `gevent` |
| Estáticos | WhiteNoise (`CompressedManifestStaticFilesStorage`) |
| Frontend | Django Templates + JavaScript plano, **sin build step** |
| PDF (servidor) | `pypdf` — trunca a las primeras 5 páginas |
| Previsualización (navegador) | PDF.js, docx-preview, SheetJS (vendorizados) |
| Despliegue | Docker + Dokku (ver [DEPLOY.md](DEPLOY.md)) |

No hay Node ni bundler: todo el JavaScript propio es ES5 plano en
`files/static/files/js/`, y las librerías de terceros están vendorizadas con su
versión fijada en un comentario de cabecera.

---

## Puesta en marcha local

```bash
# 1. Dependencias
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configuración
cp .env.example .env        # editar DATABASE_URL y SECRET_KEY

# 3. Base de datos
python manage.py migrate
python manage.py createsuperuser

# 4. Estáticos (obligatorio: STORAGES usa manifiesto, ver "Errores comunes")
python manage.py collectstatic --noinput

# 5. Servidor
python manage.py runserver
```

No hay servicios adicionales que levantar: ni Redis, ni colas, ni workers.
La aplicación solo necesita PostgreSQL.

### Variables de entorno

Se leen del `.env` de la raíz vía `django-environ` (ver `.env.example`).

| Variable | Obligatoria | Descripción |
|---|---|---|
| `SECRET_KEY` | Sí | Clave secreta de Django |
| `DEBUG` | No (`False`) | Nunca `True` en producción |
| `ALLOWED_HOSTS` | Sí en prod | Lista separada por comas |
| `DATABASE_URL` | Sí | Ej. `postgresql://user:pass@host:5432/db` |
| `MAX_UPLOAD_SIZE_MB` | No (`300`) | Tope de subida; también define el tope de previsualización de PDF |

---

## Estructura del proyecto

```
core/            Configuración del proyecto (settings, urls, wsgi)
  views.py       Dashboard y /healthz/ (usado por el healthcheck de Dokku)
files/           App de dominio
  models.py      ClassName, ActivityType, Customer, Person, ArchiveClass, FileArchive
  views.py       Listado master-detail, subida, preview, descarga, edición, alta rápida
  uploads.py     Reglas compartidas de subida (tope de tamaño, sellado de metadatos)
  forms.py       Formulario del Admin
  frontend_forms.py  Formularios del frontend propio
  admin.py       Admin personalizado
  static/files/  CSS y JS propios + librerías vendorizadas en js/vendor/
  templates/     Plantillas de la app
authentication/  Acceso y permisos por grupo (sin modelos propios)
templates/       base.html, app_base.html, dashboard.html
static/          CSS global, tema claro/oscuro, logos
```

---

## Funcionalidades

### Subida con creación de entidades sobre la marcha — `/archivos/subir/`

Formulario multipart normal (funciona sin JavaScript) que el navegador
intercepta con `XMLHttpRequest` para mostrar **barra de progreso real**;
`fetch()` no expone el progreso de subida de forma fiable.

- Arrastrar y soltar, pegado con `Ctrl+V` y selección clásica desembocan en el
  mismo `<input type="file">` real, poblado mediante la API `DataTransfer`.
- Junto a los desplegables **Cliente** y **Clase de archivo** hay botones
  `+ Nuevo` que abren un modal. El modal envía por `fetch` a su endpoint,
  inserta la opción creada en el `<select>` y la deja seleccionada, **sin
  recargar la página ni perder lo ya escrito** en el formulario principal.
- El modal de **Nuevo cliente** lleva a su vez botones `+` junto a *Clase de
  cliente* y *Tipo de actividad*: **modales anidados**, hasta dos niveles. Se
  apilan lógicamente (`data-stack-depth`), no en el DOM, y `Escape` cierra solo
  el de arriba para no descartar el formulario padre a medio llenar.
- Los desplegables largos se convierten en un **combobox con búsqueda**
  (`hf_searchable_select.js`, filtrado sin distinguir acentos ni mayúsculas). El
  `<select>` nativo permanece como fuente de verdad, así que la página sigue
  funcionando sin JavaScript.
- Los campos obligatorios llevan **asterisco rojo** derivado de
  `field.field.required` (nunca escrito a mano) más la leyenda "Los campos con *
  son obligatorios". Al enviar con datos faltantes se pinta el error bajo cada
  campo y aparece un **toast** de resumen (`hf_toast.js`).
- Al guardar, la respuesta es JSON (`{"success": true, "redirect_url": …}`)
  cuando la petición es AJAX, o una redirección clásica si no lo es. El
  archivo queda disponible de inmediato: no hay estado "pendiente".

### Vista dividida con previsualización — `/archivos/`

Interfaz master-detail: listado a la izquierda, panel de previsualización a la
derecha.

- **Un clic** selecciona la fila y previsualiza. La cabecera del panel se llena
  al instante desde atributos `data-*` ya renderizados (sin petición extra); solo
  los bytes del archivo se piden, con un *debounce* de 150 ms.
- **Doble clic** abre la edición de metadatos, si el usuario tiene el permiso
  `files.change_filearchive`. No es una función oculta: el mismo acceso está
  como botón "Editar" en el panel.
- **Teclado**: `Tab` para enfocar filas, `Enter`/`Espacio` para previsualizar,
  flechas arriba/abajo para moverse.
- **Móvil (<768px)**: el panel colapsa en un *drawer* de pantalla completa que
  se cierra con la X, el fondo o `Escape`.
- La selección es enlazable: la URL lleva `?preview=<uuid>`.

### Visor por tipo de archivo

| Tipo | Cómo se muestra | Límite |
|---|---|---|
| PNG, JPG, JPEG, WEBP, GIF | Imagen completa, centrada | 20 MB |
| PDF | PDF.js renderiza páginas en `<canvas>` | **5 páginas** (el servidor trunca antes de enviar) |
| TXT | Texto plano en `<pre>` | Primeros 16 KB |
| DOCX | docx-preview en iframe aislado | 5 páginas |
| XLSX | SheetJS (primera hoja) en iframe aislado | 20 MB |
| DOC y cualquier otro | Tarjeta con metadatos y botón de descarga | — |

Todo camino de error termina en la misma tarjeta elegante: la interfaz nunca
queda en blanco ni vuelca errores a la consola.

> **Sobre los PDF pesados:** no se usa streaming por rangos HTTP. `FileResponse`
> de Django 4.2 no soporta *Range requests* (llegó en Django 5.0). En su lugar
> el servidor **recorta el PDF a 5 páginas con `pypdf`** y cachea el resultado
> en disco, así que el navegador recibe unos pocos MB en lugar del archivo
> completo. Un PDF real de 118 MB y 257 páginas se recorta en ~0,1 s y viaja
> como 2,7 MB.

### Filtros del listado

Búsqueda por nombre y **rangos de fechas** de apertura y vencimiento, todos
combinables. Los filtros conviven en un único formulario,
así que aplicar uno nunca descarta los demás, y la paginación los conserva.

| Parámetro | Ejemplo |
|---|---|
| `q` | `?q=convenio` |
| `opening_date_from` / `opening_date_to` | `?opening_date_from=2025-01-01` |
| `due_date_from` / `due_date_to` | `?due_date_to=2025-12-31` |
| `preview` | `?preview=<uuid>` (selecciona una fila) |

Una fecha con formato inválido se ignora en silencio: no se pasa al ORM ni se
vuelve a mostrar en el formulario.

---

## Endpoints

| Método | Ruta | Nombre | Descripción |
|---|---|---|---|
| GET | `/` | `dashboard` | Panel con métricas |
| GET | `/healthz/` | `healthz` | Healthcheck (Dokku) |
| GET/POST | `/login/`, `/logout/` | `authentication:*` | Acceso |
| GET | `/archivos/` | `files:archive-list` | Vista dividida con filtros |
| GET/POST | `/archivos/subir/` | `files:archive-upload` | Subida |
| GET | `/archivos/<uuid>/preview/` | `files:archive-preview` | Contenido para el visor |
| GET | `/archivos/<uuid>/descargar/` | `files:archive-download` | Descarga del original |
| GET/POST | `/archivos/<uuid>/editar/` | `files:archive-update` | Edición de metadatos |
| POST | `/archivos/<uuid>/eliminar/` | `files:archive-delete` | Borrado |
| GET | `/clases-cliente/` | `files:classname-list` | Mantenimiento del catálogo (sin alta) |
| GET/POST | `/clases-cliente/<pk>/editar/` | `files:classname-update` | Edición |
| POST | `/clases-cliente/<pk>/eliminar/` | `files:classname-delete` | Borrado (avisa del CASCADE) |
| GET | `/tipos-actividad/` | `files:activitytype-list` | Mantenimiento del catálogo (sin alta) |
| GET/POST | `/tipos-actividad/<pk>/editar/` | `files:activitytype-update` | Edición |
| POST | `/tipos-actividad/<pk>/eliminar/` | `files:activitytype-delete` | Borrado (avisa del CASCADE) |
| POST | `/api/customers/quick-create/` | `files:customer-quick-create` | Alta rápida de cliente → `{id, label}` |
| POST | `/api/archive-classes/quick-create/` | `files:archive-class-quick-create` | Alta rápida de clase de archivo → `{id, label}` |
| POST | `/api/class-names/quick-create/` | `files:classname-quick-create` | Alta rápida de clase de cliente → `{id, label}` |
| POST | `/api/activity-types/quick-create/` | `files:activitytype-quick-create` | Alta rápida de tipo de actividad → `{id, label}` |

Clientes y personas exponen el CRUD habitual bajo `/clientes/` y `/personas/`.

Los dos catálogos de cliente (`ClassName`, `ActivityType`) **no tienen pantalla
de alta**, a propósito: solo se necesitan a media tarea, mientras se rellena un
cliente o una subida, y una pantalla aparte obligaba a abandonar el formulario a
medio llenar. Su alta vive únicamente en el modal de alta rápida; lo que queda
bajo `/clases-cliente/` y `/tipos-actividad/` es la superficie de
mantenimiento, a la que se llega desde el pie de ese modal (no desde el
sidebar, del que se retiraron). `ArchiveClass` va más lejos y no tiene ninguna
pantalla propia.

Los cuatro endpoints `quick-create` responden `201` con `{"id": …, "label": …}`
o `400` con `{"errors": {campo: [...]}}`, y exigen CSRF (`X-CSRFToken`) y el
permiso de alta del modelo correspondiente. Los tres de catálogo aceptan
`name` y `description`, y rechazan un `name` que ya exista sin distinguir
mayúsculas (`name` no lleva `unique=True` en la base: ver la deuda técnica).

---

## Permisos

El grupo **"Usuarios estándar"** se crea y mantiene solo (ver
`authentication/signals.py`): otorga `add` y `change` sobre los seis modelos de
`MANAGED_MODELS` (Customer, Person, FileArchive, ArchiveClass, ClassName y
ActivityType), **nunca `delete`** ni `view`. Todo usuario nuevo que no sea
superusuario entra a ese grupo automáticamente.

Los dos catálogos necesitan `add` justamente porque el modal es su única vía de
alta: sin ese permiso, un usuario estándar no podría crear el catálogo que su
propio formulario le exige.

Ver y descargar archivos solo requiere estar autenticado: no hay noción de
"clientes propios" por usuario.

```bash
python manage.py ensure_standard_group   # idempotente
```

---

## Pruebas

```bash
python manage.py test               # todo
python manage.py test files         # solo la app de archivos
```

La suite cubre la superficie sensible: whitelist de previsualización, cabeceras
de seguridad, truncado y cache de PDF, permisos de edición, validación de
subida y filtros por fecha. Los tests usan siempre un `MEDIA_ROOT` temporal;
**nunca** escriben en el `media/` real.

También fija lo que es fácil romper en silencio: que las rutas de alta
retiradas sigan devolviendo 404 y su nombre sin resolver; que las listas de
catálogo rendericen **con** el permiso de alta (un `{% url %}` a una ruta
inexistente levanta `NoReverseMatch` *al renderizar*, es decir un 500 que solo
se ve si el `{% if perms %}` se evalúa); que `Customer.name` sea obligatorio en
los tres caminos de alta; que los endpoints de catálogo guarden de verdad la
`description`; y que `data-target-select` siga apuntando al id que genera el
ModelForm.

---

## Errores comunes

**`ValueError: Missing staticfiles manifest entry`** — falta ejecutar
`python manage.py collectstatic`. `STORAGES` usa el almacenamiento con
manifiesto de WhiteNoise, así que todo estático nuevo debe recogerse antes de
poder referenciarlo.

**Un archivo aparece en la lista pero da 404 al previsualizar o descargar** —
la fila existe en la base de datos pero sus bytes no están en disco (volumen
`media/` sin montar, o borrado externo). Queda registrado como
`FileArchive <id>: bytes ausentes en disco` en los logs del servidor. No es
reparable desde la aplicación: hay que volver a subir el archivo.

**Los archivos desaparecen al desplegar** — falta el montaje persistente de
`media/`; ver [DEPLOY.md](DEPLOY.md).

---

## Dependencias sin usar

`requirements.txt` conserva comentado un bloque de paquetes que están
instalados pero **no se usan en ninguna parte** del código: el stack de API REST
(`djangorestframework`, `simplejwt`, `django-rest-authtoken`,
`django-cors-headers`, `django-filter`) y el soporte MySQL (`pymysql`,
`cryptography`). No están en `INSTALLED_APPS` ni se importan.

Quedan comentados, no borrados, porque la decisión es de producto: si la API
REST está en la hoja de ruta, se descomentan; si no, se eliminan esas líneas.

---

## Documentación relacionada

- [ARCHITECTURE.md](ARCHITECTURE.md) — decisiones de diseño y por qué
- [DEPLOY.md](DEPLOY.md) — despliegue en Dokku paso a paso
