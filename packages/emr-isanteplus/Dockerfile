from ghcr.io/isanteplus/docker-isanteplus-server:v2.3.4

add ./config/custom_modules /custom_modules

# Fix: the base image's startup.sh unconditionally overwrites openmrs-runtime.properties
# with a template that lacks encryption keys. This breaks OpenMRS on subsequent restarts
# because it can't decrypt stored credentials and falls into the setup wizard loop.
# This patched version only overwrites if encryption keys are not present (i.e. first boot).
RUN sed -i 's|if \[ -f \$OMRS_RUNTIME_PROPERTIES_FILE \]; then|if [ -f $OMRS_RUNTIME_PROPERTIES_FILE ] \&\& ! grep -q "encryption.key" $OMRS_RUNTIME_PROPERTIES_FILE; then|' \
    /usr/local/tomcat/startup.sh

# Post-start script: runs in the background after OpenMRS boots to set
# per-instance global properties (mpi-client.pid.local, xds-sender endpoints).
# These cannot be baked into the shared MySQL dump because each instance needs unique values.
COPY ./config/post-start.sh /usr/local/tomcat/post-start.sh
RUN chmod +x /usr/local/tomcat/post-start.sh

# Inject the post-start script into startup.sh so it runs in the background
# after OpenMRS is up but before the final `wait` that holds the container open.
RUN sed -i '/# bring tomcat process to foreground again/i \
/usr/local/tomcat/post-start.sh \&\n' /usr/local/tomcat/startup.sh