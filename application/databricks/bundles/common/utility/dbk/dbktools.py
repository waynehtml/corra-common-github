# Databricks notebook source
if "_OEDS_DBK_TOOLS_LOADED" not in globals():
    _OEDS_DBK_TOOLS_LOADED = True

    # Get Job/Notebook parameters
    def param(name, default=None):
        """
        Retrieve a parameter value passed to a Databricks notebook.

        This helper function tries to obtain the value of a parameter from two possible sources:
        1. **Notebook bindings** — when the notebook is executed as part of a Databricks Job,
            parameters are passed via `dbutils.notebook.entry_point.getCurrentBindings()`.
        2. **Widgets** — when running interactively in the Databricks UI, parameters are often 
            provided through widgets (e.g., created with `dbutils.widgets.text`).

        If the parameter cannot be found in either place, the function returns the provided default value.

        Args:
            name (str): The name of the parameter to retrieve.
            default (Any, optional): A fallback value to return if the parameter is not found. Defaults to None.

        Returns:
            str | Any: The parameter value (stripped of leading/trailing whitespace) or the default value.
        """

        try:
            # Attempt to read parameter from notebook bindings (used in Databricks jobs)
            args = dbutils.notebook.entry_point.getCurrentBindings()
            if name in args:
                return args[name].strip()
        except Exception:
            # Ignore errors if bindings are not available (e.g., running interactively)
            pass

        try:
            # Attempt to read parameter from Databricks widget (used in interactive runs)
            return dbutils.widgets.get(name).strip()
        except Exception:
            # Return default if widget not defined or widget API not available
            return default

    # Add python utils to sys.path
    #python_utils_dir = "/Workspace/oedataservices/.bundle/common/files/utility/python/src"
    #if python_utils_dir not in sys.path:
    #    sys.path.append(python_utils_dir)


    # Checking for required packages
    #import oeds.core.package_utils as package
    #required_packages = ["azure-core", "azure-identity", "pandas"]
    #package.ensure_packages_installed(required_packages, if_not_exist="warning")
