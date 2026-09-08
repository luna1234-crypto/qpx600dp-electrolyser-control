# Delivered project tree

Extract the archive contents into the requested `CPX 400 DP` directory.
The `cpx_electrolyser/` subdirectory is the importable Python package.
Packages containing only `__init__.py` are reserved boundaries for later work.
The virtual environment is created locally and is not distributed.

```text
CPX 400 DP/
    .git/  (Git history; two milestone commits in the final archive)
    config/
        README.md
    cpx_electrolyser/
        app/
            __init__.py
            application.py
            logging_config.py
            paths.py
        data/
            __init__.py
        engine/
            protocols/
                __init__.py
            __init__.py
        gui/
            plots/
                __init__.py
            test_setup/
                __init__.py
            __init__.py
            main_window.py
        instrument/
            __init__.py
        safety/
            __init__.py
        __init__.py
        __main__.py
    Data/
        .gitkeep
    docs/
        reference/
            cpx400dp_manual_issue1.pdf
        verification/
            milestone1.md
            milestone1_window.png
            milestone2.md
        architecture.md
        cpx400dp_commands.md
        milestones.md
        project_tree.md
    examples/
        README.md
    logs/
        .gitkeep
    tests/
        manual_hardware_test.md
        manual_smoke_test.md
        test_application.py
        test_logging_config.py
    .gitattributes
    .gitignore
    main.py
    pyproject.toml
    README.md
    requirements-dev.txt
    requirements.txt
    Start_App.cmd
```

Application logs and future experiment data are ignored by Git. No test data
is bundled as though it came from an instrument.
