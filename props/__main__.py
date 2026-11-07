import sys
import warnings

# macOS system Python ships LibreSSL; urllib3 warns about it on every run. Harmless.
warnings.filterwarnings("ignore", message=".*OpenSSL.*")

from .cli import main  # noqa: E402

sys.exit(main())
