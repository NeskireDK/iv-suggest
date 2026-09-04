FROM python:3.13-alpine

# psql is the transport: `query` parses the CSV this client prints, so the
# client stays and only the connection changed. The major is pinned because
# that is what alpine keeps; an exact version pin breaks on every repo bump.
RUN apk add --no-cache postgresql16-client \
 && pip install --no-cache-dir PyYAML==6.0.2

COPY --chmod=0755 iv-suggest /usr/local/bin/iv-suggest

# The deploy check reads this label. There is no file on the box to hash any
# more, so the revision is the only thing that says what is running.
ARG REVISION=unknown
LABEL org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.source=https://github.com/NeskireDK/iv-suggest \
      org.opencontainers.image.description="Lane based suggestion playlists for Invidious"

# It reads two files and talks to two sockets, and writes nothing to disk.
# nologin is not a security property -- /bin/sh is still in the image and
# `docker exec` ignores a login shell. What enforces this is `read_only`,
# `cap_drop` and `no-new-privileges` in compose.iv-suggest.yml.
RUN adduser -D -H -s /sbin/nologin -u 10001 iv-suggest
USER 10001

# So a run says which code produced it. The image label is the deploy check;
# this is what puts the same answer in the journal, which is what the old
# md5-the-binary check used to give and a label alone does not.
ENV IV_SUGGEST_REVISION=$REVISION

ENTRYPOINT ["/usr/local/bin/iv-suggest"]
