import logging
import azure.functions as func
import os
import time
import base64
import requests
import json
import pathspec
from concurrent.futures import ThreadPoolExecutor, as_completed
from sailpoint.v3.api import (
    service_desk_integration_api,
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

@app.timer_trigger(schedule="0 33 15 * * *", arg_name="myTimer", run_on_startup=False,
                   use_monitor=False)
def sailpoint_backup(myTimer: func.TimerRequest) -> None:
    if myTimer.past_due:
        logging.info('The timer is past due!')

    start_time = time.time()
    BASE_DIR = "."
    EXPORTS_DIR = "."
    os.makedirs(os.path.join(BASE_DIR, EXPORTS_DIR), exist_ok=True)
    configuration = Configuration()

    config_items = {
        "SERVICE_DESK_INTEGRATION": [],
        "RULE": [],
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
                "SERVICE_DESK_INTEGRATION": service_desk_integration_api.ServiceDeskIntegrationApi(client).get_service_desk_integrations,
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

    def save_json(obj, directory, name):
        os.makedirs(directory, exist_ok=True)
        safe_name = name.replace(" ", "_").replace("/", "_").replace("#", "_")
        path = os.path.join(directory, f"{safe_name}.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(obj.model_dump_json(indent=4))
        logging.info(f"📁 Saved '{name}' → {path}")

    def download_result(api, job_id, config_type, item_name, item_id):
        try:
            if config_type == "RULE":
                rule = connector_rule_management_api.ConnectorRuleManagementApi(api.api_client).get_connector_rule(id=item_id)
                save_json(rule, os.path.join(BASE_DIR, EXPORTS_DIR, config_type), item_name)
                return

            result = api.get_sp_config_export(id=job_id)
            if not result.objects:
                logging.warning(f"⚠️ No objects for {config_type} '{item_name}'")
                return

            for obj in result.objects:
                obj_name = safe(obj.var_self, "name") or safe(obj, "name", "id") or "Unnamed"
                save_json(obj, os.path.join(BASE_DIR, EXPORTS_DIR, config_type), obj_name)
        except Exception as e:
            logging.error(f"❌ Failed to download {config_type} '{item_name}': {e}")

    # --- Run Exports in Parallel ---
    def export_item(api, config_type, item):
        logging.info(f"🔄 Exporting {config_type}: {item['name']}")
        try:
            job_id = start_export_job(api, config_type, [item["id"]])
            if wait_for_completion(api, job_id):
                download_result(api, job_id, config_type, item["name"], item["id"])
        except Exception as e:
            logging.error(f"❌ Failed {config_type} '{item['name']}': {e}")

    with BetaApiClient(configuration) as client:
        api = sp_config_api.SPConfigApi(client)
        tasks = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            for config_type, items in config_items.items():
                for item in items:
                    tasks.append(executor.submit(export_item, api, config_type, item))

            for future in as_completed(tasks):
                future.result()

    # --- GitHub Commit & Push via requests ---
    def commit_exports_only_requests(exports_dir):
        github_token = os.getenv("GITHUB_TOKEN")
        if not github_token:
            raise RuntimeError("❌ GITHUB_TOKEN env variable not set")

        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        }

        def upload_file(file_path, repo_path):
            with open(file_path, "rb") as f:
                content = base64.b64encode(f.read()).decode()

            url = f"https://api.github.com/repos/thedevops-mohi/iam-sailpoint/contents/{repo_path}?ref=main"
            resp = requests.get(url, headers=headers)
            sha = resp.json()["sha"] if resp.status_code == 200 else None

            data = {
                "message": "Automated export of SailPoint configs",
                "content": content,
                "branch": "main"
            }
            if sha:
                data["sha"] = sha

            r = requests.put(url, headers=headers, data=json.dumps(data))
            if r.status_code in [200, 201]:
                logging.info(f"✅ Uploaded '{repo_path}' successfully")
            else:
                logging.error(f"❌ Failed to upload '{repo_path}': {r.status_code} {r.text}")

        # Load .gitignore rules
        gitignore_path = os.path.join(BASE_DIR, ".gitignore")
        spec = pathspec.PathSpec.from_lines("gitwildmatch", open(gitignore_path))

        # Walk exports directory and upload only files not ignored
        for root, dirs, files in os.walk(exports_dir):
            # Skip ignored dirs
            dirs[:] = [d for d in dirs if not spec.match_file(os.path.relpath(os.path.join(root, d), exports_dir))]
            for file in files:
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, exports_dir)
                if spec.match_file(relative_path):
                    continue
                # Prepend folder name in repo so structure is preserved
                repo_path = os.path.join(exports_dir, relative_path).replace("\\", "/")
                upload_file(local_path, repo_path)

    commit_exports_only_requests(os.path.join(BASE_DIR, EXPORTS_DIR))

    elapsed_time = time.time() - start_time
    logging.info(f"Script completed in {elapsed_time:.2f} seconds ({elapsed_time/60:.2f} minutes)")
