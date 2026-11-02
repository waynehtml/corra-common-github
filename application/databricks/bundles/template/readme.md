# Template Bundle

This bundle is a starter project for Databricks-based data processing on top of the OEDS/OEDIS platform. It provides a runnable workflow skeleton, packaged configuration, reusable Python utilities, release notebooks, and examples that show how to build a dataset pipeline with ingestion, storage movement, schema management, permissions, notifications, and time series registration.

## What the template includes

- A Databricks Asset Bundle job in [application/databricks/bundles/template/databricks.yml](/C:/repo/template/application/databricks/bundles/template/databricks.yml) named `OEDS_SAMPLE_JOB`.
- A staged workflow in [application/databricks/bundles/template/tasks](/C:/repo/template/application/databricks/bundles/template/tasks) with these task slots:
  - `main`
  - `ingest`
  - `land_to_raw`
  - `raw_to_std`
  - `std_to_curated`
  - `on_complete`
  - `on_failure`
- Shared notebook bootstrap logic in [application/databricks/bundles/template/common.py](/C:/repo/template/application/databricks/bundles/template/common.py) that builds runtime context, resolves secrets, configures logging, adds the bundle `src` directory to `sys.path`, and initializes the email manager.
- A packaged Python module in [application/databricks/bundles/template/src/oeds_workflow](/C:/repo/template/application/databricks/bundles/template/src/oeds_workflow) for reusable workflow code and configuration.
- Release notebooks in [application/databricks/bundles/template/releases](/C:/repo/template/application/databricks/bundles/template/releases) for schema creation, sample data loading, and timeseries registration.
- Example notebooks in [application/databricks/bundles/template/examples](/C:/repo/template/application/databricks/bundles/template/examples) that demonstrate common operational tasks.
- Cluster bootstrap assets in [application/databricks/bundles/template/cluster](/C:/repo/template/application/databricks/bundles/template/cluster) and wheelhouse maintenance tools in [application/databricks/bundles/template/tools](/C:/repo/template/application/databricks/bundles/template/tools).

## Runtime flow

The bundle job is designed as a gated multi-step pipeline.

1. `task_00_main.py` prepares shared context and computes `file_date` from `rundate`.
2. `task_10_ingest.py` calls the DIS ingestion service through `oeds.domain.dis.client.DisClient`.
3. `task_20_land_to_raw.py` moves landed files from the landing zone into the raw zone.
4. `task_30_raw_to_std.py` is a scaffold for raw-to-standardized processing.
5. `task_40_std_to_curated.py` is a scaffold for standardized-to-curated processing.
6. `task_90_on_complete.py` runs when the flow completes successfully or exits early in a controlled way.
7. `task_91_on_failure.py` sends a failure email.

The job uses Databricks task values to control flow. Each major task can set `flow_action`, and the `main` notebook can short-circuit the pipeline by setting `flow_action=complete` after running a release or maintenance notebook through the `task` parameter.

## Core features discovered in the code

### 1. DIS-backed ingestion

The ingest stage loads packaged JSON from [src/oeds_workflow/config/ingest/default.json](/C:/repo/template/application/databricks/bundles/template/src/oeds_workflow/config/ingest/default.json), resolves template variables such as `env` and `land_dir`, and invokes the OEDIS ingestion job. The sample config shows a simple two-step flow:

- Download source files from a Unity Catalog volume.
- Upload them into the landing directory for the current dataset and file date.

After the DIS run succeeds, the pipeline verifies that files exist in the landing directory and checks for CSV content.

### 2. Landing and raw storage layout

The bundle expects standard storage roots passed through bundle variables:

- `land_root`
- `config_root`
- `raw_root`

The default path pattern is:

- `.../dis/template/data/land/<dataset>/<file_date>`
- `.../dis/template/data/raw/<dataset>/<file_date>`

The land-to-raw stage physically moves the full landed folder into raw storage using `oeds.core.file_utils.move_full_folder`.

### 3. Packaged configuration model

Instead of keeping operational config inside notebooks, the template packages JSON and Python definitions under [src/oeds_workflow/config](/C:/repo/template/application/databricks/bundles/template/src/oeds_workflow/config):

- `ingest`: ingestion service templates
- `email`: email registry and HTML templates
- `permission`: Unity Catalog grants by schema reference and environment
- `schema`: table and view definitions as Python `DEFINITION` objects
- `timeseries`: timeseries provider, dataset, and series registration metadata
- `file`: placeholder area for file-related registry code

This makes the workflow easier to extend without turning notebooks into large config containers.

### 4. Schema registry and release automation

`SchemaManager` in [src/oeds_workflow/utils/schema/schema_utils.py](/C:/repo/template/application/databricks/bundles/template/src/oeds_workflow/utils/schema/schema_utils.py) discovers schema definitions from the packaged config and can:

- List registered tables and views
- Resolve a schema reference to a fully qualified Unity Catalog name
- Create Delta tables from `StructType` definitions
- Render table DDL with generated identity columns and column comments
- Create views with placeholder substitution such as `{tbl_sample}`

The sample release notebooks use this manager to create all configured tables and views.

Included sample schema objects:

- `tbl_sample`
- `tbl_ts`
- `vw_sample`

### 5. Permission management

`PermissionManager` in [src/oeds_workflow/utils/permission/permission_utils.py](/C:/repo/template/application/databricks/bundles/template/src/oeds_workflow/utils/permission/permission_utils.py) applies Unity Catalog grants from packaged JSON.

Notable behavior:

- Catalog names are normalized per environment, for example `dev_application_cat`.
- Groups are resolved from aliases like `aeeng` and `dataopen`.
- Read permissions for views include dependent objects, so a consumer can query the view successfully.
- Write permissions are supported for tables only.

The sample permission registry grants:

- write access on `tbl_sample` to `aeeng`
- read access on `tbl_ts` and `vw_sample` to `dataopen` and `aeeng`

### 6. Email notifications

The common bootstrap initializes an `EmailManager`, and the failure notebook uses it to send a packaged failure message.

The email utility supports:

- Registry-based email definitions from JSON
- HTML templates packaged with the code
- Recipient alias resolution through `job_notify_emails`
- Variable substitution through the OEDS email service
- Optional attachments
- DataFrame-to-HTML conversion for simple tabular email content

The sample config currently defines a `failure` template that targets `DM` and `DDA` recipients.

### 7. Time series support

`TimeSeriesUtils` in [src/oeds_workflow/utils/timeseries/timeseries_utils.py](/C:/repo/template/application/databricks/bundles/template/src/oeds_workflow/utils/timeseries/timeseries_utils.py) integrates with the OEDS timeseries manager and supports:

- Registering providers, datasets, and series from packaged JSON
- Resolving destination UC tables through the schema registry
- Updating observations in a target Delta table
- Publishing a registered batch to configured datamart endpoints

The sample timeseries config registers:

- provider `BoC`
- dataset `template.ds1`
- series `template.ts1`
- destination table `tbl_ts`

### 8. Operational maintenance entry points

The `main` notebook supports direct execution of release or utility notebooks by passing:

- `task`: relative notebook path or full `/Workspace/...` path
- `args`: JSON arguments for the target notebook

This allows the same job to run both scheduled pipeline steps and controlled maintenance/release actions.

### 9. Wheelhouse and cluster bootstrap support

The bundle includes:

- [cluster/init.sh](/C:/repo/template/application/databricks/bundles/template/cluster/init.sh)
- [cluster/init_wheelhouse.sh](/C:/repo/template/application/databricks/bundles/template/cluster/init_wheelhouse.sh)
- [cluster/requirements.txt](/C:/repo/template/application/databricks/bundles/template/cluster/requirements.txt)
- [tools/build_wheelhouse.py](/C:/repo/template/application/databricks/bundles/template/tools/build_wheelhouse.py)
- [tools/remove_wheelhouse.py](/C:/repo/template/application/databricks/bundles/template/tools/remove_wheelhouse.py)

These assets support dependency caching from Nexus so clusters can install from a prepared wheelhouse instead of downloading packages repeatedly.

## Important bundle parameters

The bundle definition exposes variables for:

- dataset naming
- environment and date formatting
- landing, config, and raw roots
- secret scope and secret keys
- internal and external Databricks workspace connection info
- DIS job ids
- datamart endpoints
- Nexus package index
- cluster init and wheelhouse paths
- job notification email mappings

The `dev` target is currently the most fully populated target. `stg` and `prd` mainly override workspace mode and debug behavior.

## Main folders

- [application/databricks/bundles/template/tasks](/C:/repo/template/application/databricks/bundles/template/tasks): notebook tasks that define the job flow
- [application/databricks/bundles/template/src/oeds_workflow](/C:/repo/template/application/databricks/bundles/template/src/oeds_workflow): reusable workflow code and packaged config
- [application/databricks/bundles/template/releases](/C:/repo/template/application/databricks/bundles/template/releases): release and initialization notebooks
- [application/databricks/bundles/template/examples](/C:/repo/template/application/databricks/bundles/template/examples): examples for permissions, email, calendar, and timeseries usage
- [application/databricks/bundles/template/tools](/C:/repo/template/application/databricks/bundles/template/tools): operator utilities
- [application/databricks/bundles/template/cluster](/C:/repo/template/application/databricks/bundles/template/cluster): cluster init scripts and Python requirements
- [application/databricks/bundles/template/script/pre](/C:/repo/template/application/databricks/bundles/template/script/pre) and [application/databricks/bundles/template/script/post](/C:/repo/template/application/databricks/bundles/template/script/post): deployment hook samples

## How to extend this template

To turn this into a real project, the intended customization points are:

1. Replace the sample ingest config with the source-system requests your dataset needs.
2. Implement the placeholder transform notebooks for `raw_to_std` and `std_to_curated`.
3. Add schema definitions under `src/oeds_workflow/config/schema`.
4. Add permission rules under `src/oeds_workflow/config/permission/default.json`.
5. Add email templates and registry entries under `src/oeds_workflow/config/email`.
6. Add timeseries config if the dataset publishes statistical series.
7. Update bundle variables and cluster settings for the target environments.
8. Add tests in [application/databricks/bundles/template/tests](/C:/repo/template/application/databricks/bundles/template/tests) as the workflow becomes project-specific.

## Current limitations in the template

The codebase is intentionally incomplete in a few places:

- `task_30_raw_to_std.py` and `task_40_std_to_curated.py` are placeholders.
- Some release notebook headers still refer to older release names.
- A few paths and variable names contain typos such as `conifg` and `secrete`, which appear consistently in the current template.

Those gaps are useful to know because this repository is best understood as a working starter bundle rather than a finished end-to-end product.

