# SAM makefile build target (BuildMethod: makefile in template.yaml).
#
# Key constraints:
# 1. Build runs on macOS arm64 but Lambda is Amazon Linux aarch64. Packages with
#    native extensions use --platform manylinux2014_aarch64 so Linux .so files are
#    fetched rather than macOS dylibs. Pure Python packages install normally.
# 2. webull-python-sdk-quotes-core and webull-python-sdk-trade-events-core both
#    hard-pin grpcio==1.51.1 (no cp312 arm64 wheels, not needed at runtime — Lambda
#    uses the REST path only). Installed with --no-deps to skip grpcio entirely.
# 3. webull SDK vendors ancient requests+urllib3+six (Python 3.12-incompatible).
#    Vendored requests is replaced with modern requests; vendored six.py is replaced
#    with modern six that implements find_spec.

PY      := PYENV_VERSION=3.12.13 python3.12
LINUX   := --platform manylinux2014_aarch64 --python-version 312 --implementation cp --only-binary=:all:

build-SignalFunction:
	# Packages with native extensions — fetch Linux aarch64 wheels explicitly.
	$(PY) -m pip install --quiet $(LINUX) \
		"google-api-python-client>=2.0" \
		"google-auth>=2.0" \
		"google-auth-oauthlib>=1.0" \
		"requests>=2.31" \
		"cryptography>=2.6.0" \
		"protobuf==4.21.12" \
		--target "$(ARTIFACTS_DIR)"
	# Pure Python packages — no platform flags needed.
	$(PY) -m pip install --quiet \
		"jmespath>=0.9.3,<1.0.0" \
		"cachetools==5.2.0" \
		"paho-mqtt==1.6.1" \
		"six" \
		--target "$(ARTIFACTS_DIR)"
	# webull SDK packages — pure Python (py3-none-any), --no-deps skips grpcio.
	$(PY) -m pip install --quiet --no-deps \
		"webull-python-sdk-core==0.1.18" \
		"webull-python-sdk-trade==0.1.18" \
		"webull-python-sdk-trade-events-core==0.1.18" \
		"webull-python-sdk-mdata==0.1.18" \
		"webull-python-sdk-quotes-core==0.1.18" \
		--target "$(ARTIFACTS_DIR)"
	cp -r . "$(ARTIFACTS_DIR)"
	# Fix webull vendored deps (Python 3.12-incompatible).
	rm -rf "$(ARTIFACTS_DIR)/webullsdkcore/vendored/requests"
	cp -r "$(ARTIFACTS_DIR)/requests" "$(ARTIFACTS_DIR)/webullsdkcore/vendored/requests"
	cp "$(ARTIFACTS_DIR)/six.py" "$(ARTIFACTS_DIR)/webullsdkcore/vendored/six.py"
