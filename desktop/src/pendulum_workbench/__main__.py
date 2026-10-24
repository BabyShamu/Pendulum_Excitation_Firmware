import sys

from pendulum_workbench.app import main

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--shared-matlab-runner":
        from pendulum_workbench.experiments.shared_matlab_runner import main as runner_main

        raise SystemExit(runner_main(sys.argv[1:]))
    raise SystemExit(main())
