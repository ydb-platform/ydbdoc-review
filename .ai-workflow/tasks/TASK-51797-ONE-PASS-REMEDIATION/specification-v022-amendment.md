## v021 metadata race closure v022

This amendment changes no Ruff or product behavior. It closes only two protocol
fields added while the v021 reviewer was claiming the request:

1. each of the three v021 exact manifest mappings has literal
   `mapping_source: v021_exact_R004_Ruff_artifact_mapping`;
2. v021 control-path resolution explicitly includes
   `.ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v020.yaml` in
   addition to the four v021 protocol paths. The path remains byte-immutable and
   is not duplicated in the final manifest.

Everything else is the externally APPROVED v021 contract. v022 resolves exact
predecessor v021, seals its own four files plus `response-v021.yaml`, then uses
the unchanged v021 Ruff validation, refresh and immediate policy validation.

