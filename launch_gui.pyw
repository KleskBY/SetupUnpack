import sys

if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
    from setupunpack.selftest import run
    raise SystemExit(run(sys.argv[2]))
else:
    from setupunpack.gui import main
    main()
