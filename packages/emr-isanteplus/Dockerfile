from ghcr.io/isanteplus/docker-isanteplus-server:v2.3.4

add ./config/custom_modules /custom_modules

# Fix: the base image's startup.sh unconditionally overwrites openmrs-runtime.properties
# with a template that lacks encryption keys. This breaks OpenMRS on subsequent restarts
# because it can't decrypt stored credentials and falls into the setup wizard loop.
# This patched version only overwrites if encryption keys are not present (i.e. first boot).
RUN sed -i 's|if \[ -f \$OMRS_RUNTIME_PROPERTIES_FILE \]; then|if [ -f $OMRS_RUNTIME_PROPERTIES_FILE ] \&\& ! grep -q "encryption.key" $OMRS_RUNTIME_PROPERTIES_FILE; then|' \
    /usr/local/tomcat/startup.sh