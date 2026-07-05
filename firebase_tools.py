"""
firebase_tools.py — generates Firebase-backed app boilerplate.

Firebase project creation itself (enabling auth providers, creating the
Firestore database, getting the config object) has to happen in the
Firebase console — no API can do that part for you. What THIS tool does
is generate the code that plugs into a project you've already created:
auth wiring (signup/login/logout), Firestore read/write helpers, and
security rules matching whatever data shape you describe.

Reuses the same atomic write_file() from tools.py so generated files get
the same crash-safety guarantee as everything else the agent writes.
"""

from tools import write_file


FIREBASE_CONFIG_TEMPLATE = """// firebase-config.js
// Paste your project's config here — get it from:
// Firebase Console > Project Settings > General > Your apps > SDK setup and config
import {{ initializeApp }} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import {{ getAuth }} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import {{ getFirestore }} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";

const firebaseConfig = {{
  apiKey: "YOUR_API_KEY",
  authDomain: "YOUR_PROJECT.firebaseapp.com",
  projectId: "YOUR_PROJECT_ID",
  storageBucket: "YOUR_PROJECT.appspot.com",
  messagingSenderId: "YOUR_SENDER_ID",
  appId: "YOUR_APP_ID"
}};

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export const db = getFirestore(app);
"""

AUTH_TEMPLATE = """// auth.js — email/password signup, login, logout
import {{
  createUserWithEmailAndPassword,
  signInWithEmailAndPassword,
  signOut,
  onAuthStateChanged
}} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import {{ auth }} from "./firebase-config.js";

export async function signUp(email, password) {{
  try {{
    const cred = await createUserWithEmailAndPassword(auth, email, password);
    return {{ success: true, user: cred.user }};
  }} catch (err) {{
    return {{ success: false, error: err.message }};
  }}
}}

export async function logIn(email, password) {{
  try {{
    const cred = await signInWithEmailAndPassword(auth, email, password);
    return {{ success: true, user: cred.user }};
  }} catch (err) {{
    return {{ success: false, error: err.message }};
  }}
}}

export async function logOut() {{
  await signOut(auth);
}}

// Call this once on page load to react to login/logout state
export function watchAuthState(onLoggedIn, onLoggedOut) {{
  onAuthStateChanged(auth, (user) => {{
    if (user) onLoggedIn(user);
    else onLoggedOut();
  }});
}}
"""

FIRESTORE_HELPERS_TEMPLATE = """// db.js — read/write helpers for collection: {collection_name}
import {{
  collection, doc, setDoc, getDoc, getDocs, updateDoc, deleteDoc, query, where
}} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";
import {{ db }} from "./firebase-config.js";

const COLLECTION = "{collection_name}";

export async function createDoc(id, data) {{
  await setDoc(doc(db, COLLECTION, id), data);
}}

export async function getDocById(id) {{
  const snap = await getDoc(doc(db, COLLECTION, id));
  return snap.exists() ? snap.data() : null;
}}

export async function getAllDocs() {{
  const snap = await getDocs(collection(db, COLLECTION));
  return snap.docs.map(d => ({{ id: d.id, ...d.data() }}));
}}

export async function getDocsWhereOwner(userId, ownerField="ownerId") {{
  const q = query(collection(db, COLLECTION), where(ownerField, "==", userId));
  const snap = await getDocs(q);
  return snap.docs.map(d => ({{ id: d.id, ...d.data() }}));
}}

export async function updateDocById(id, data) {{
  await updateDoc(doc(db, COLLECTION, id), data);
}}

export async function deleteDocById(id) {{
  await deleteDoc(doc(db, COLLECTION, id));
}}
"""

FIRESTORE_RULES_TEMPLATE = """rules_version = '2';
service cloud.firestore {{
  match /databases/{{database}}/documents {{
    match /{collection_name}/{{docId}} {{
      // Only signed-in users can read; only the owner can write/edit/delete.
      // Every document in this collection MUST have an "{owner_field}" field
      // matching the creator's auth uid for these rules to work correctly.
      allow read: if request.auth != null;
      allow create: if request.auth != null
                    && request.resource.data.{owner_field} == request.auth.uid;
      allow update, delete: if request.auth != null
                    && resource.data.{owner_field} == request.auth.uid;
    }}
  }}
}}
"""


def scaffold_firebase_app(collection_name="items", owner_field="ownerId", output_dir="."):
    """
    Generates a full set of Firebase starter files into output_dir:
    - firebase-config.js (paste-your-config placeholder)
    - auth.js (signup/login/logout/state-watcher)
    - db.js (CRUD helpers scoped to `collection_name`)
    - firestore.rules (security rules requiring login + ownership)

    Returns a summary string plus manual setup steps the human still has
    to do in the Firebase console (no API can do these programmatically).
    """
    files_written = []

    path = f"{output_dir}/firebase-config.js".replace("//", "/")
    write_file(path, FIREBASE_CONFIG_TEMPLATE)
    files_written.append(path)

    path = f"{output_dir}/auth.js".replace("//", "/")
    write_file(path, AUTH_TEMPLATE)
    files_written.append(path)

    path = f"{output_dir}/db.js".replace("//", "/")
    write_file(path, FIRESTORE_HELPERS_TEMPLATE.format(collection_name=collection_name))
    files_written.append(path)

    path = f"{output_dir}/firestore.rules".replace("//", "/")
    write_file(path, FIRESTORE_RULES_TEMPLATE.format(collection_name=collection_name, owner_field=owner_field))
    files_written.append(path)

    manual_steps = (
        "Files generated:\n  " + "\n  ".join(files_written) + "\n\n"
        "Manual steps still needed in the Firebase console (console.firebase.google.com):\n"
        "  1. Create a project (or use an existing one).\n"
        "  2. Project Settings > General > Your apps > add a Web app, copy the\n"
        "     config values into firebase-config.js (replace the YOUR_* placeholders).\n"
        "  3. Build > Authentication > Sign-in method > enable 'Email/Password'.\n"
        "  4. Build > Firestore Database > Create database (start in production mode).\n"
        "  5. Firestore Database > Rules tab > paste in the contents of firestore.rules,\n"
        "     then click Publish.\n"
    )
    return manual_steps


# --- Tool schema addition, merge into tools.TOOL_SCHEMA / TOOL_FUNCTIONS ---
FIREBASE_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "scaffold_firebase_app",
        "description": "Generate Firebase auth + Firestore boilerplate files (config, auth.js, "
                        "db.js, firestore.rules) for a web app that needs real user login and "
                        "a per-user private database. Use this whenever a task needs accounts "
                        "or persistent user data, not just a static site.",
        "parameters": {"type": "object", "properties": {
            "collection_name": {"type": "string", "description": "Firestore collection name, e.g. 'todos', 'posts'", "default": "items"},
            "owner_field": {"type": "string", "description": "Field name storing the creator's user id", "default": "ownerId"},
            "output_dir": {"type": "string", "default": "."},
        }},
    }},
]

FIREBASE_TOOL_FUNCTIONS = {
    "scaffold_firebase_app": scaffold_firebase_app,
}
