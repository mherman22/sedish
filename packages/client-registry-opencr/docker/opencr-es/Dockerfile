# opencr-es — Elasticsearch for the OpenCR Client Registry, pinned to the EXACT matching stack that
# OpenCR (DIGI-UW/client-registry-fork) is built and tested against:
#   Elasticsearch 7.9.1 + string-similarity-scoring v0.0.6 (+ analysis-phonetic)
#
# The string-similarity-scoring plugin is what actually EXECUTES the decision-rule algorithms inside
# Elasticsearch (damerau-levenshtein, jaro-winkler-similarity, normalized-levenshtein-similarity).
# Without it, every match 500s and NO decision rule fires.
#
# We build our own image (instead of the floating, ~5-year-old intrahealth/elasticsearch:latest) so
# the matching stack is REPRODUCIBLE and self-owned: the ES version and the exact plugin build are
# both fixed here, and the plugin zip is vendored in-repo (no dependency on GitHub reachability or a
# mutable upstream tag that could be repushed without the plugin). v0.0.6 is the latest plugin release
# (Aug 2021); it ships per-ES builds and 7.9.1 is the one OpenCR uses.
FROM elasticsearch:7.9.1

# Phonetic analyzer (installed by the fork's ES image too).
RUN /usr/share/elasticsearch/bin/elasticsearch-plugin install --batch analysis-phonetic

# The similarity-scoring plugin — vendored zip, installed from disk (not downloaded at build time).
COPY string-similarity-scoring-0.0.6-es7.9.1.zip /tmp/string-similarity-scoring.zip
RUN /usr/share/elasticsearch/bin/elasticsearch-plugin install --batch file:///tmp/string-similarity-scoring.zip \
    && rm -f /tmp/string-similarity-scoring.zip
