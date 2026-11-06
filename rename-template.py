#!/usr/bin/env python3
"""
SPDX-FileCopyrightText: 2025 DESY and the Constellation authors
SPDX-License-Identifier: EUPL-1.2
"""

import argparse
import os
import pathlib


def file_replace(old_text: str, new_text: str, file_path: pathlib.Path) -> None:
    with open(file_path) as file:
        text = file.read()
        text = text.replace(old_text, new_text)
    with open(file_path, "w") as file:
        file.write(text)


def rename_template(new_type: str) -> None:
    wd = pathlib.Path(__file__).parent
    src = wd.joinpath("src")
    src_template = src.joinpath("Template")

    # Rename class
    file_replace("Template", new_type, src_template.joinpath("Template.py"))
    file_replace("Template", new_type, src_template.joinpath("__init__.py"))
    file_replace("Template", new_type, src_template.joinpath("__main__.py"))
    file_replace("Template", new_type, wd.joinpath("pyproject.toml"))

    # Rename folder and class file
    os.rename(src_template.joinpath("Template.py"), src_template.joinpath(new_type + ".py"))
    os.rename(src_template, src.joinpath(new_type))

    # Remove default URLs
    file_replace("https://constellation.pages.desy.de/", "TODO", wd.joinpath("pyproject.toml"))
    file_replace("https://gitlab.desy.de/constellation/templates/satellite-py", "TODO", wd.joinpath("pyproject.toml"))

    # Remove the rename script
    os.remove(__file__)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("new_type")
    args = parser.parse_args()
    rename_template(args.new_type)
