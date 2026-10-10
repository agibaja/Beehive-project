# Beehive

Beehive manages office desks and meeting rooms. Administrators create buildings, floors and spaces, upload floor plans and review room detections from a trained segmentation model. Employees request spaces through a Google Calendar add-on. The Python backend stores bookings in Supabase and assigns compatible spaces using an optimizer.

## Project structure

```text
beehive/
├── backend/
│   ├── api/
│   │   ├── main.py
│   │   ├── bookings.py
│   │   ├── admin.py
│   │   ├── database.py
│   │   ├── schemas.py
│   │   └── security.py
│   ├── optimizer/
│   │   ├── optimizer.py
│   │   ├── service.py
│   │   ├── booking_repository.py
│   │   ├── supabase_connection.py
│   │   └── .env                 # Local backend configuration
│   └── sql/
│       ├── 001_safe_booking_save.sql
│       ├── 002_employee_auth.sql
│       ├── 003_google_identity.sql
│       ├── 004_admin.sql
│       └── 005_plan_import.sql
├── admin_app/
│   ├── app.py
│   ├── plan_workflow.py
│   ├── inference.py
│   ├── .env                     # Local admin app configuration
│   └── models/
│       └── best_floorplan_unet.pth
├── calendar_addon/
│   ├── Code.gs
│   └── appsscript.json
└── README.md
```

Keep Python package `__init__.py` files in the existing backend folders. The files in `calendar_addon/` are a copy of the Google Apps Script source; editing them locally does not update Google automatically.

## 1. Install the required software and packages

These instructions use Windows and Anaconda Prompt. Replace `C:\Users\ainho\Downloads\beehive` with your actual project location.

Required services: a Supabase project, a Google account with access to Apps Script and a manageable Google Cloud project. Internet access is needed for authentication, Supabase, Calendar and the tunnel.

### Python environment

If the environment does not exist, create it once:

```bat
conda create -n beehive python=3.11 -y
conda activate beehive
```

If it already exists, just activate it:

```bat
conda activate beehive
```

Install the application's packages:

```bat
python -m pip install fastapi "uvicorn[standard]" "pydantic>=2,<3" supabase python-dotenv requests google-auth ortools streamlit pandas Pillow python-multipart torch torchvision segmentation-models-pytorch albumentations opencv-python-headless
```

These are installation instructions, not a locked reproducible environment. For model compatibility, use the PyTorch, torchvision, segmentation-models-pytorch and Albumentations versions used during training when available. Consult the PyTorch installation selector for an installation matched to your GPU; CPU inference also works. Do not install both OpenCV headless and desktop variants in the same environment.

Once your environment works, record its versions:

```bat
python -m pip freeze > requirements.txt
```

### Cloudflare tunnel client

Install `cloudflared` separately; it is not a Python package. Follow the [official installation instructions](https://developers.cloudflare.com/tunnel/downloads/), then verify:

```bat
cloudflared --version
```

Cloudflare is needed for Calendar to reach the local backend. It is not needed to use the admin app locally.

### Database setup

The provided SQL files are incremental migrations. They require the original tables and relationships already used by Beehive: `company`, `employee`, `building`, `floor`, `space` and `booking`. They do not create the complete base schema from an empty project.

For an existing Beehive database, apply only missing migrations in numerical order through Supabase → SQL Editor. Migrations 004 and 005 provide administrative management and transactional room import. Do not rebuild an existing database just to start the app.

## 2. Configure and open the admin app

### Backend configuration

Create or retain `backend/optimizer/.env`:

```env
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_KEY=YOUR_BACKEND_SECRET_OR_SERVICE_ROLE_KEY
COMPANY_ID=YOUR_COMPANY_UUID
GOOGLE_ADDON_CLIENT_ID=YOUR_APPS_SCRIPT_OAUTH_AUDIENCE
```

`COMPANY_ID` is the fallback for manually running the optimizer. Authenticated API operations obtain the company from the employee record. `GOOGLE_ADDON_CLIENT_ID` is required for Calendar authentication; the admin app uses a different sign-in mechanism.

### Admin app configuration

Create `admin_app/.env`:

```env
BACKEND_URL=http://127.0.0.1:8000
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_ANON_KEY=YOUR_PUBLIC_ANON_OR_PUBLISHABLE_KEY
```

Find the public key in Supabase → Project Settings → API Keys. A publishable key starting with `sb_publishable_` can be placed in `SUPABASE_ANON_KEY`; retain this variable name because the app reads it. Do not put the backend secret key in this field.

The two `.env` files have different purposes. Keep each in its stated location.

### Administrator account

1. In Supabase → Authentication → Users, create a user with an email and password. Ensure the email is confirmed for the demo if confirmation is required.
2. Copy its Authentication user UUID.
3. Find the corresponding employee in Table Editor → `employee`.
4. Set its `auth_user_id` to that UUID, set `role` to `admin`, and ensure `company_id` points to the correct company.
5. Sign in to Streamlit using the Authentication user's email and password.

You can link an existing employee instead of creating another employee. Creating an Authentication user alone does not grant admin access.

### Terminal 1: start the backend

```bat
conda activate beehive
cd C:\Users\ainho\Downloads\beehive\backend
python -m uvicorn api.main:app --reload
```

Keep this terminal open. Check [backend health](http://127.0.0.1:8000/health); it should return `{"status":"ok"}`.

### Terminal 2: start Streamlit

```bat
conda activate beehive
cd C:\Users\ainho\Downloads\beehive
python -m streamlit run admin_app/app.py --server.address 127.0.0.1
```

Open [the admin app](http://localhost:8501) and sign in. Keep both terminals running.

### Admin workflow

1. Open **Planos** (Floor plans).
2. Select an existing building or create one using **Crear edificio**.
3. Select an existing floor or create one using **Crear planta**.
4. Upload a PNG/JPG plan, up to 15 MB.
5. Choose the minimum region area and click **Analizar plano**.
6. Review the numbered regions and edit the proposed spaces in the table.
7. Set names, type, capacity, equipment, accessibility and window attributes. For fixed spaces, provide an employee ID belonging to the same company.
8. Uncheck **Guardar** for unwanted detections. Add rows for missed rooms or individual desks.
9. Click **Guardar espacios seleccionados** to create the selected spaces.
10. Click **Guardar plano** to retain the image. Saving spaces and saving the image are separate operations.

Each `desk` has capacity 1. Room capacity starts at 1 in the review table: replace it with the real capacity. The model does not infer capacity or equipment.

Use **Edificios**, **Plantas** and **Espacios** to manage existing records. Editing a space with active or future reservations is blocked in the current implementation.

## 3. Configure and open Google Calendar

### One-time Google setup

For the simplest development demo, use the same personal Google account for Cloud, Apps Script, installation and Calendar. A university account may restrict Cloud or add-on installation. Google documents a same-domain requirement for testers of unpublished Workspace add-ons; do not assume that sharing a project or adding an OAuth test user permits cross-domain installation.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create/select the **Beehive** project.
2. Open APIs & Services → Library → Google Calendar API → Enable.
3. Configure Google Auth Platform with the application name and contact email. For a personal demo, use External audience, Testing status, and add the Calendar account under Test users.
4. Copy the project **number**, not its textual project ID. It is available under IAM & Admin → Settings.
5. Open [Apps Script](https://script.google.com/home). Create/open Beehive and copy the add-on JavaScript into `Code.gs`/`Código.gs`.
6. In Project Settings, enable **Show appsscript.json manifest file in editor**. Paste the project's manifest into that file.
7. In Project Settings → Google Cloud Platform Project → Change project, enter the project number. Changing the Cloud project requires reauthorization and cannot restore the former automatic project.
8. Confirm that the manifest includes `https://www.googleapis.com/auth/calendar.events` in `oauthScopes` and `https://www.googleapis.com/calendar/v3/` in `urlFetchWhitelist`. Retain the other scopes, add-on settings and triggers from the supplied manifest.

This implementation calls Calendar using `UrlFetchApp`, so adding the advanced Calendar service in Apps Script is not required. Calendar API must still be enabled in the linked Cloud project.

### Terminal 3: expose the backend to Apps Script

With Terminal 1 still running:

```bat
cloudflared tunnel --url http://127.0.0.1:8000
```

Keep this terminal open and copy the printed `https://...trycloudflare.com` URL.

In the correct Apps Script project:

1. Project Settings → Script properties: set `BACKEND_URL` to the new URL.
2. In `appsscript.json`, update the Cloudflare entry in `urlFetchWhitelist` and retain the Calendar API entry:

```json
"urlFetchWhitelist": [
  "https://YOUR-CURRENT-TUNNEL.trycloudflare.com/",
  "https://www.googleapis.com/calendar/v3/"
]
```

This is a manifest fragment, not the complete file. Include the trailing slash in the whitelist. A Quick Tunnel can provide a different URL after each restart, so update both locations when necessary. The Streamlit `.env` can continue pointing to localhost.

### Link identity and install the test add-on

1. In Apps Script, run `showIdentityConfiguration()` using the Google account that will use Calendar.
2. In the execution log, read `client_id` and `google_sub`. Never copy or share the complete identity/access token.
3. Put `client_id` in `GOOGLE_ADDON_CLIENT_ID` in `backend/optimizer/.env`.
4. Put `google_sub` in the correct employee's `google_sub` column in Supabase, as described below.
5. Restart the backend if its `.env` changed. A Supabase employee change does not require a backend restart.
6. Run `checkBackendIdentity()`. It should report the intended employee and company.
7. Using the Calendar account, open Deploy → Test deployments → Install → Done. The tester needs editor access to the script project and must meet Google's account/domain requirements.
8. Open [Google Calendar](https://calendar.google.com/) with that same account and refresh the page. Open Beehive in the side panel and grant requested permissions.

Installing a test add-on under one account does not install it under another account. The event is created in the Calendar of the user who authorizes and uses the add-on. The OAuth `client_id` identifies the application; `google_sub` identifies the Google user.

### Test a booking

1. Submit a new request for a future time with requirements that an existing desk/room can meet.
2. If the booking is `assigned`, check that its event appears with the correct time and location.
3. If event creation fails, open **Mis reservas** → select that existing booking → **Crear / actualizar evento**. Do not submit a duplicate booking.
4. To release the space, use Beehive → **Cancelar reserva** → confirmation. If event deletion fails, use the provided retry button.

Streamlit does not need to be running to submit Calendar bookings. The backend and tunnel do.

## 4. Add a new employee

### Employee who will book spaces in Calendar

1. Open Supabase → Table Editor → `employee` → Insert row.
2. Enter the employee name, any other required fields in your actual schema, and the existing company's UUID in `company_id`. Use the database-generated employee ID if the table provides a default.
3. Choose the ordinary employee role allowed by your database. Use `admin` only for administrators; the uploaded migrations do not establish every permitted ordinary-role value.
4. Have the employee authorize the add-on and run `showIdentityConfiguration()` under their own Google account in the development setup. This requires permitted project access and account/domain configuration.
5. Copy their `google_sub` into this employee row and save. Although it consists of digits, it is a text identifier: keep every digit unchanged.
6. Add their account as an OAuth test user when the Cloud app is External/Testing, and install the test add-on under their account if eligible.
7. Run `checkBackendIdentity()` under that account to confirm the employee association, then test a booking.

Each Google account needs its own employee association. `google_sub` is unique when migration 003 is applied. Replacing an existing employee's value moves that association to the new account; it does not retain both accounts.

### Employee who also needs admin app access

1. Create the employee record or use an existing one.
2. Create their email/password user in Supabase → Authentication → Users.
3. Copy the Authentication UUID into that employee's `auth_user_id`.
4. Set the employee role to `admin` and verify its company.
5. Have them sign in to Streamlit.

If they also use Calendar, populate their `google_sub` separately. Calendar users do not need a Supabase Authentication password solely to book through the add-on.

| Identifier | Where it is stored | Purpose |
|---|---|---|
| Employee `id` | `employee.id` | Internal employee record and booking ownership |
| Company UUID | `employee.company_id` | Company whose buildings/spaces the user can access |
| Google `sub` | `employee.google_sub` | Calendar user's Google identity |
| Supabase Auth UUID | `employee.auth_user_id` | Admin app email/password identity |
| OAuth audience/client ID | Backend `.env`: `GOOGLE_ADDON_CLIENT_ID` | Google application identity accepted by the backend |

## 5. Model and operating notes

The model file must be at `admin_app/models/best_floorplan_unet.pth`, unless `MODEL_PATH` in `inference.py` is deliberately changed. It contains the trained state dictionary for a U-Net with ResNet34 encoder, 3 input channels and 3 output classes: background, room and wall.

Inference converts the image to RGB, resizes it to 512×512, applies the validation normalization and converts it to a tensor. Mean is `(0.485, 0.456, 0.406)` and standard deviation is `(0.229, 0.224, 0.225)`. Training flips, rotations and affine augmentation are not used at inference.

The class mask is resized back to the original image using nearest-neighbor interpolation. Connected regions of class 1 are counted; the default minimum area is 120 original-image pixels. Connected rooms can be counted together and fragmented regions separately. The administrator must review the proposals. Desks are added manually.

The room-import transaction is all-or-nothing. Retry unchanged input from the same browser session to retain its request identifier. Changing the input, restarting the session or importing the same plan again can create a new import and equivalent spaces. The current version stores the spaces' floor relationship, not their detected contours or coordinates.

New spaces are considered in the next backend optimization. Do not run `optimizer.py` separately for normal API booking operations. Automatic background synchronization of optimizer-driven room changes into Calendar has not been implemented; use **Crear / actualizar evento** to refresh the event. Manual event deletion or time changes in Calendar do not update the reservation in Supabase.

### Daily startup and shutdown

| Task | Processes required |
|---|---|
| Manage spaces locally | Backend + Streamlit |
| Book through Calendar | Backend + Cloudflare |
| Demonstrate both interfaces | All three |

Use Ctrl+C in a terminal to stop its process. Restart Streamlit after changing the model path or weights if necessary. Restart the backend after changing its `.env`. Keep the computer and processes running while the demo is in use.

### Troubleshooting

| Symptom | What to check |
|---|---|
| Google account is not linked to an employee | Match the executing account's `google_sub` to the intended employee row |
| Invalid/expired Google identity | Correct OAuth client audience in backend `.env`, reauthorize and restart backend after configuration changes |
| Calendar API disabled/not used | Enable Calendar API in the Cloud project actually linked to Apps Script |
| Insufficient authentication scopes | Check `calendar.events` in the manifest and grant it; use the existing `resetAuthorization()` helper if present, then authorize again |
| Old tunnel URL appears | Check script properties and manifest in the installed project; Calendar may be opening the old Beehive project |
| Add-on installation fails | Copy the exact error; verify the active account, editor access, OAuth test users and domain restrictions |
| Streamlit login fails | Authentication user confirmation, `auth_user_id`, admin role, environment values and running backend |
| Model not found | Filename and path in `inference.py`; the weights are not included in code-only updates |
| Space update rejected | Active/future bookings, required fields, positive capacity and fixed-space ownership |
| Backend does not start | Correct working directory, packages, package `__init__.py` files and `backend/optimizer/.env` |

### Credentials and deployment

Keep `.env` files, backend secret keys, passwords and tokens out of GitHub, including private repositories. Public keys and OAuth client IDs are not backend secrets. Revoke exposed secret keys rather than only deleting them from a file. A suitable `.gitignore` is:

```gitignore
.env
.env.*
*.env
!.env.example
__pycache__/
*.py[cod]
.venv/
venv/
*.pth
```

The current setup is a local development/demo workflow, not a production deployment. Quick Tunnels, test add-on installations and session-memory state should be replaced with a deployment plan before broader use. Google Cloud currently provides the Calendar API/OAuth configuration; it does not host this Python backend.

## Official documentation

- [Cloudflare installation](https://developers.cloudflare.com/tunnel/downloads/)
- [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
- [Apps Script Cloud projects](https://developers.google.com/apps-script/guides/cloud-platform-projects)
- [Google Calendar API setup](https://developers.google.com/workspace/calendar/api/quickstart/python)
- [Testing Workspace add-ons](https://developers.google.com/workspace/add-ons/how-tos/testing-workspace-addons)
- [Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys)
- [PyTorch installation](https://pytorch.org/get-started/locally/)

This guide describes the supplied implementation and the setup developed for this project. Database migrations, account permissions and model compatibility must be checked in the target environment.
