# Delivered project tree

Extract the corrected archive contents into a `QPX 600 DP` directory.
The current macOS checkout may retain its legacy outer `CPX400DP_Project(1)`
container name, but no tracked runtime code depends on it.
The `qpx_electrolyser/` subdirectory is the importable Python package.
Packages containing only `__init__.py` are reserved boundaries for later work.
The virtual environment is created locally and is not distributed.

```text
QPX 600 DP/
    .git/  (Git history; one stable commit per completed milestone)
    config/
        README.md
    qpx_electrolyser/
        app/
            __init__.py
            application.py
            channel_controller.py
            connection_controller.py
            logging_config.py
            paths.py
        data/
            __init__.py
            csv_logging.py
        engine/
            constant_current.py
            protocols/
                __init__.py
            __init__.py
        gui/
            plots/
                __init__.py
                realtime.py
            test_setup/
                __init__.py
            __init__.py
            connection_panel.py
            channel_panel.py
            constant_current_panel.py
            main_window.py
        instrument/
            __init__.py
            _validation.py
            base_power_supply.py
            capabilities.py
            channel_readback.py
            connection_worker.py
            exceptions.py
            hardware_check.py
            qpx600dp.py
            serial_transport.py
            simulator.py
            simulator_config.py
        safety/
            __init__.py
        __init__.py
        __main__.py
    Data/
        .gitkeep
    docs/
        reference/
            README.md
            qpx600_addendum_48511-1461_issue1.pdf
            qpx600d_dp_instruction_manual_48511-1460_issue14.pdf
            superseded/
                README.md
                cpx400dp_manual_issue1.pdf
        verification/
            milestone1.md
            milestone1_window.png
            milestone2.md
            milestone3.md
            milestone4.md
            milestone5.md
            milestone6.md
            milestone7.md
            milestone7_real_hardware.md
            milestone8.md
            milestone9.md
            milestone10.md
            model_correction_qpx600dp.md
        architecture.md
        qpx600dp_commands.md
        milestones.md
        project_tree.md
        simulator.md
    examples/
        __init__.py
        README.md
        simulator_demo.py
    logs/
        .gitkeep
    tests/
        manual_hardware_test.md
        manual_smoke_test.md
        test_application.py
        test_capabilities.py
        test_channel_gui.py
        test_connection_gui.py
        test_connection_worker.py
        test_constant_current.py
        test_csv_logging.py
        test_hardware_check.py
        test_logging_config.py
        test_model_identity.py
        test_qpx600dp_driver.py
        test_real_hardware_gui.py
        test_realtime_plot.py
        test_serial_transport.py
        test_simulator.py
    .gitattributes
    .gitignore
    main.py
    pyproject.toml
    README.md
    requirements-dev.txt
    requirements.txt
    Start_App.cmd
```

Application logs and future experiment data are ignored by Git. The console
example produces explicitly simulated readings and does not create CSV data.
