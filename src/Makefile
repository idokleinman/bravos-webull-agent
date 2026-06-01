# SAM makefile build target (BuildMethod: makefile in template.yaml).
#
# webull-python-sdk-quotes-core and webull-python-sdk-trade-events-core both
# hard-pin grpcio==1.51.1 which has no cp312 arm64 wheels and is not needed
# at runtime (the Lambda uses the REST path, never gRPC). Install webull
# packages with --no-deps to skip grpcio, then install the real non-grpc
# transitive deps explicitly.

PY := PYENV_VERSION=3.12.13 python3.12

build-SignalFunction:
	$(PY) -m pip install --quiet \
		"google-api-python-client>=2.0" \
		"google-auth>=2.0" \
		"google-auth-oauthlib>=1.0" \
		"requests>=2.31" \
		--target "$(ARTIFACTS_DIR)"
	$(PY) -m pip install --quiet --no-deps \
		"webull-python-sdk-core==0.1.18" \
		"webull-python-sdk-trade==0.1.18" \
		"webull-python-sdk-trade-events-core==0.1.18" \
		"webull-python-sdk-mdata==0.1.18" \
		"webull-python-sdk-quotes-core==0.1.18" \
		--target "$(ARTIFACTS_DIR)"
	$(PY) -m pip install --quiet \
		"jmespath>=0.9.3,<1.0.0" \
		"cryptography>=2.6.0" \
		"cachetools==5.2.0" \
		"protobuf==4.21.12" \
		"paho-mqtt==1.6.1" \
		--target "$(ARTIFACTS_DIR)"
	cp -r . "$(ARTIFACTS_DIR)"
	# webull SDK vendors ancient requests+urllib3+six, all Python 3.12-incompatible.
	# Replace vendored requests with the modern version already installed, and patch
	# vendored six.py with modern six (find_spec required in Python 3.12).
	$(PY) -m pip install --quiet six --target "$(ARTIFACTS_DIR)"
	rm -rf "$(ARTIFACTS_DIR)/webullsdkcore/vendored/requests"
	cp -r "$(ARTIFACTS_DIR)/requests" "$(ARTIFACTS_DIR)/webullsdkcore/vendored/requests"
	cp "$(ARTIFACTS_DIR)/six.py" "$(ARTIFACTS_DIR)/webullsdkcore/vendored/six.py"
