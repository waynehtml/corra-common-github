# Databricks notebook source
# DBTITLE 1,Inlude common routine for examples
# MAGIC %run ./__common__

# COMMAND ----------

# DBTITLE 1,Import Common Python Packages
import oeds.core.file_utils as fileutils

# COMMAND ----------

# DBTITLE 1,Set up testing environment
def setup():
    show("Setup testing environment")
    
    global root_path
    root_path = os.path.join(test_file_root, "file")
    global src_path
    src_path = os.path.join(root_path, "src")
    global dst_path
    dst_path = os.path.join(root_path, "dst")

    # clean up
    dbutils.fs.rm(root_path, True)

    global file1
    file1 = os.path.join(src_path, "file1.txt")
    dbutils.fs.put(file1, file1, True)
    
    global file2
    file2 = os.path.join(src_path, "file2.txt")
    dbutils.fs.put(file2, file2, True)

    global file3
    file3 = os.path.join(src_path, "file3.log")
    dbutils.fs.put(file3, file3, True)

    global child_path
    child_path = os.path.join(src_path, "file_dir")

    global file11
    file11 = os.path.join(child_path, "file11.txt")
    dbutils.fs.put(file11, file11, True)

    global file12
    file12 = os.path.join(child_path, "file12.log")
    dbutils.fs.put(file12, file12, True)

# COMMAND ----------

from dbruntime.dbutils import FileInfo
from typing import List

def show_paths(files: List[FileInfo], title="") -> None:
    """
    Print sorted paths from a list of FileInfo objects.

    Parameters
    ----------
    files : list[FileInfo]
        Array of FileInfo objects (from dbutils.fs.ls or similar).
    """
    if len(title) > 0:
        print(f"{title}:")
    for fi in sorted(files, key=lambda f: f.path):
        print(fi.path)

def snapshot(title):
    print(f"\n# {title} #")
    show_paths(fileutils.ls(src_path, recursive=True, item_type="all"), f"Files/folders in src: {src_path}")
    print("")
    show_paths(fileutils.ls(dst_path, recursive=True, item_type="all"), f"Files/folders in dst: {dst_path}")

# COMMAND ----------

# Setup testing environment
setup()
# Check if file1 is a file 
show(fileutils.is_file(file1), {file1})
# Check if root_path is a directory
show(fileutils.is_dir(root_path), {root_path})

# COMMAND ----------

# Setup testing environment
setup()
# Check if file1 is a file 
show(fileutils.exists(file1), {file1})
# Check if root_path is a directory
show(fileutils.exists(root_path), {root_path})

# COMMAND ----------

# Setup testing environment
setup()
# List all .txt files under root_path recursively
items = fileutils.ls(root_path, pattern="*.txt", recursive=True, item_type="files")
show_paths(items,"List all .txt files under root_path recursively") 

# COMMAND ----------

# Setup testing environment
setup()
# List all folders like file* under root_path recursively
items = fileutils.ls(root_path, pattern="file*", recursive=True, item_type="dirs")
show_paths(items, "List all folders like file* under root_path recursively")

# COMMAND ----------

# Setup testing environment
setup()
# List all files and directories under root_path recursively with names starting with "file"
items = fileutils.ls(root_path, pattern="file*", recursive=True, item_type="all")
show_paths(items, "List all files and directories under root_path recursively with names starting with \"file\"")

# COMMAND ----------

# Setup testing environment
setup()
# Remove all files under src_path (non-recursive)
fileutils.delete_selected_files(src_path, recursive=False)
snapshot("Remove all files under src_path (non-recursive)")

# COMMAND ----------

# Setup testing environment
setup()
# Remove all files under src_path (recursive)
fileutils.delete_selected_files(src_path, recursive=True)
snapshot("Remove all files under src_path (recursive)")

# COMMAND ----------

# Setup testing environment
setup()
# Copy a single file (file2) to the dst directory with a different name
fileutils.copy_single_file(file2, f"{dst_path}/file2_1.txt")
snapshot("Copy a single file (file2) to the dst directory with a different name")

# Copy a single file (file1) to the dst directory
fileutils.copy_single_file(file1, dst_path)
snapshot("Copy a single file (file1) to the dst directory")

# COMMAND ----------

# Setup testing environment
setup()
# Copy all txt files under src_path to dst_path recursively
fileutils.copy_selected_files(src_path, dst_path,"*.txt",True)
snapshot("Copy all txt files under src_path to dst_path recursively")

# COMMAND ----------

# Setup testing environment
setup()
# Copy all files under src_path to dst_path recursively
fileutils.copy_full_folder(src_path, dst_path)
snapshot("Copy all files under src_path to dst_path recursively")

# COMMAND ----------

# Setup testing environment
setup()
# move a single file (file2) to the dst directory with a different name
fileutils.move_single_file(file2, f"{dst_path}/file2_1.txt")
snapshot("Move a single file (file2) to the dst directory with a different name")

# move a single file (file1) to the dst directory
fileutils.move_single_file(file1, dst_path)
snapshot("Move a single file (file1) to the dst directory")

# COMMAND ----------

# Setup testing environment
setup()
# Move all txt files under src_path to dst_path recursively
fileutils.move_selected_files(src_path, dst_path,"*.txt",True)
snapshot("Move all txt files under src_path to dst_path recursively")

# COMMAND ----------

# Setup testing environment
setup()
# Move all files under src_path to dst_path recursively
fileutils.move_full_folder(src_path, dst_path)
snapshot("Move all files under src_path to dst_path recursively")

# COMMAND ----------

import posixpath

# return filename as group name
def resolve_file_basename(rel_path: str) -> str:
    file_name = posixpath.basename(rel_path)
    base_name, _ = posixpath.splitext(file_name)
    return base_name

# Setup testing environment
setup()
fileutils.move_files_by_group(
    src_dir=src_path,
    dst_dir=dst_path,
    resolver=resolve_file_basename,
    file_pattern="*.txt",
    policy="default",
    recursive=True
)
