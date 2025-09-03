import logging
import azure.functions as func
import os
import time
import base64
import requests
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import pathspec
import datetime


from sailpoint.v3.api import (
    roles_api, sources_api, workflows_api, transforms_api,
    access_profiles_api, service_desk_integration_api,
    identity_profiles_api
)
from sailpoint.beta.api import (
    sp_config_api, connector_rule_management_api,
)
from sailpoint.v3.api_client import ApiClient as V3ApiClient
from sailpoint.beta.api_client import ApiClient as BetaApiClient
from sailpoint.configuration import Configuration
from sailpoint.beta.models import ExportPayload

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = func.FunctionApp()


@app.timer_trigger(schedule="0 */20 * * * *", arg_name="myTimer", run_on_startup=False,
                   use_monitor=False)
def sailpoint_backup(myTimer: func.TimerRequest) -> None:
    logging.info('The timer is past due!')
    utc_now = datetime.datetime.utcnow()
    local_now = datetime.datetime.now()
    logging.info(f"UTC now: {utc_now}, Local now: {local_now}")

    if myTimer.past_due:
        logging.info('The timer is past due!')

    start_time = time.time()
    BASE_DIR = "/tmp"   # ✅ temp dir for Azure Functions
    EXPORTS_DIR = "spconfig-exports"
    EXPORTS_PATH = os.path.join(BASE_DIR, EXPORTS_DIR)
    os.makedirs(EXPORTS_PATH, exist_ok=True)

    configuration = Configuration()

    config_items = {
        "ROLE": [], "SOURCE": [], "WORKFLOW": [], "TRANSFORM": [],
        "ACCESS_PROFILE": [], "SERVICE_DESK_INTEGRATION": [],
        "IDENTITY_PROFILE": [], "RULE": [],
    }

    # --- Helpers ---
    def safe(obj, *attrs):
        for attr in attrs:
            if hasattr(obj, attr):
                return getattr(obj, attr, None)
        return None

    def add_items(results, target):
        for item in results:
            target.append({"id": safe(item, "id"), "name": safe(item, "name")})

    # --- Collect Config Items ---
    with V3ApiClient(configuration) as client:
        try:
            api_map = {
                "ROLE": roles_api.RolesApi(client).list_roles,
                "SOURCE": sources_api.SourcesApi(client).list_sources,
                "WORKFLOW": workflows_api.WorkflowsApi(client).list_workflows,
                "TRANSFORM": transforms_api.TransformsApi(client).list_transforms,
                "ACCESS_PROFILE": access_profiles_api.AccessProfilesApi(client).list_access_profiles,
                "SERVICE_DESK_INTEGRATION": service_desk_integration_api.ServiceDeskIntegrationApi(client).get_service_desk_integrations,
                "IDENTITY_PROFILE": identity_profiles_api.IdentityProfilesApi(client).list_identity_profiles,
            }
            for key, func in api_map.items():
                add_items(func(), config_items[key])
        except Exception as e:
            logging.info(f"⚠️ Skipping V3 items: {e}")

    with BetaApiClient(configuration) as client:
        try:
            add_items(
                connector_rule_management_api.ConnectorRuleManagementApi(client).get_connector_rule_list(),
                config_items["RULE"]
            )
        except Exception as e:
            logging.info(f"❌ Exception during Beta API extraction: {e}")

    logging.info("✅ Extracted configuration items:")
    for key, items in config_items.items():
        logging.info(f"{key}: {len(items)} items")

    # --- Export Helpers ---
    def save_json(obj, directory, name):
        os.makedirs(directory, exist_ok=True)
        safe_name = name.replace(" ", "_").replace("/", "_").replace("#", "_")
        path = os.path.join(directory, f"{safe_name}.json")
        with open(path, "w", encoding="utf-8") as f:
            try:
                f.write(obj.model_dump_json(indent=4))
            except AttributeError:
                f.write(json.dumps(obj.to_dict() if hasattr(obj, "to_dict") else obj, indent=4))
        logging.info(f"📁 Saved '{name}' → {path}")

    def start_export_job(api, config_type, ids):
        payload = ExportPayload(
            description=f"Exporting {config_type}",
            include_types=[config_type],
            object_options={config_type: {"includedIds": ids}}
        )
        return api.export_sp_config(export_payload=payload).job_id

    def wait_for_completion(api, job_id, timeout=900, interval=15):
        elapsed = 0
        while elapsed < timeout:
            try:
                status = api.get_sp_config_export_status(id=job_id).status
                if status == "COMPLETE":
                    return True
                if status in ["FAILED", "CANCELLED"]:
                    raise Exception(f"Job {job_id} failed: {status}")
            except Exception as e:
                logging.warning(f"⚠️ Status check error: {e}")
            time.sleep(interval)
            elapsed += interval
        raise TimeoutError(f"Job {job_id} timed out after {timeout}s.")

    def download_result(api, job_id, config_type, item_name, item_id):
        try:
            if config_type == "RULE":
                rule = connector_rule_management_api.ConnectorRuleManagementApi(api.api_client).get_connector_rule(id=item_id)
                save_json(rule, os.path.join(EXPORTS_PATH, config_type), item_name)
                return

            result = api.get_sp_config_export(id=job_id)
            if not result.objects:
                logging.warning(f"⚠️ No objects for {config_type} '{item_name}'")
                return

            for obj in result.objects:
                obj_name = safe(obj.var_self, "name") or safe(obj, "name", "id") or "Unnamed"
                save_json(obj, os.path.join(EXPORTS_PATH, config_type), obj_name)
        except Exception as e:
            logging.error(f"❌ Failed to download {config_type} '{item_name}': {e}")

    def export_item(api, config_type, item):
        logging.info(f"🔄 Exporting {config_type}: {item['name']}")
        try:
            job_id = start_export_job(api, config_type, [item["id"]])
            if wait_for_completion(api, job_id):
                download_result(api, job_id, config_type, item["name"], item["id"])
        except Exception as e:
            logging.error(f"❌ Failed {config_type} '{item['name']}': {e}")

    # --- Run Exports in Parallel ---
    with BetaApiClient(configuration) as client:
        api = sp_config_api.SPConfigApi(client)
        tasks = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            for config_type, items in config_items.items():
                for item in items:
                    tasks.append(executor.submit(export_item, api, config_type, item))

            for future in as_completed(tasks):
                future.result()

    # --- GitHub Commit & Push with .gitignore awareness ---
    def commit_exports_batch():
        github_token = os.getenv("GITHUB_TOKEN")
        if not github_token:
            raise RuntimeError("❌ GITHUB_TOKEN env variable not set")

        owner = "thedevops-mohi"
        repo = "iam-sailpoint"
        branch = "main"

        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        }

        # Load .gitignore if present
        gitignore_path = os.path.join(BASE_DIR, ".gitignore")
        spec = None
        if os.path.exists(gitignore_path):
            with open(gitignore_path) as f:
                spec = pathspec.PathSpec.from_lines("gitwildmatch", f)

        # 1. Get latest commit on branch
        ref_url = f"https://api.github.com/repos/{owner}/{repo}/git/ref/heads/{branch}"
        ref_resp = requests.get(ref_url, headers=headers)
        ref_resp.raise_for_status()
        ref_data = ref_resp.json()
        latest_commit_sha = ref_data["object"]["sha"]

        commit_url = f"https://api.github.com/repos/{owner}/{repo}/git/commits/{latest_commit_sha}"
        commit_resp = requests.get(commit_url, headers=headers)
        commit_resp.raise_for_status()
        commit_data = commit_resp.json()
        base_tree_sha = commit_data["tree"]["sha"]

        # 2. Create blobs for each JSON file in EXPORTS_PATH
        tree_entries = []
        for root, _, files in os.walk(EXPORTS_PATH):
            for file in files:
                if not file.endswith(".json"):
                    continue
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, EXPORTS_PATH)

                # Skip if in .gitignore
                if spec and spec.match_file(relative_path):
                    logging.info(f"⏩ Skipped {relative_path} (ignored by .gitignore)")
                    continue

                with open(local_path, "rb") as f:
                    content = f.read()

                blob_url = f"https://api.github.com/repos/{owner}/{repo}/git/blobs"
                blob_resp = requests.post(blob_url, headers=headers, json={
                    "content": base64.b64encode(content).decode(),
                    "encoding": "base64"
                })
                blob_resp.raise_for_status()
                blob_sha = blob_resp.json()["sha"]

                tree_entries.append({
                    "path": relative_path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha
                })

        if not tree_entries:
            logging.info("⚠️ No JSON files to commit. Skipping push.")
            return

        # 3. Create a new tree
        tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees"
        tree_resp = requests.post(tree_url, headers=headers, json={
            "base_tree": base_tree_sha,
            "tree": tree_entries
        })
        tree_resp.raise_for_status()
        new_tree_sha = tree_resp.json()["sha"]

        # 4. Create a new commit
        new_commit_url = f"https://api.github.com/repos/{owner}/{repo}/git/commits"
        commit_message = f"Automated export of SailPoint configs ({time.strftime('%Y-%m-%d %H:%M:%S')})"
        new_commit_resp = requests.post(new_commit_url, headers=headers, json={
            "message": commit_message,
            "tree": new_tree_sha,
            "parents": [latest_commit_sha]
        })
        new_commit_resp.raise_for_status()
        new_commit_sha = new_commit_resp.json()["sha"]

        # 5. Update branch ref to point to new commit
        update_ref_url = f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/{branch}"
        update_ref_resp = requests.patch(update_ref_url, headers=headers, json={
            "sha": new_commit_sha,
            "force": False
        })
        update_ref_resp.raise_for_status()

        logging.info(f"✅ Batch commit created: {new_commit_sha}")

    # ✅ call the commit function once exports are done
    commit_exports_batch()

    elapsed_time = time.time() - start_time
    logging.info(f"Script completed in {elapsed_time:.2f} seconds ({elapsed_time/60:.2f} minutes)")
