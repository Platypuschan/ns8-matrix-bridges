#!/bin/bash

#
# Copyright (C) 2023 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

# Build one NS8 module image per bridge of bridges.json:
# ghcr.io/platypuschan/matrix-<bridge>. All images share imageroot/ and the
# UI; etc/bridge names the bridge of an image.

set -e

images=()
repobase="${REPOBASE:-ghcr.io/platypuschan}"
repository_source="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-Platypuschan/ns8-matrix-bridges}"
revision="${GITHUB_SHA:-$(git rev-parse HEAD 2>/dev/null || echo unknown)}"
# Keep in sync with the Node.js version used by .github/workflows/validate.yml
NODE_IMAGE="docker.io/library/node:24.20.0"
nodebuilder="nodebuilder-matrix-bridges-${NODE_IMAGE##*:}"

if ! buildah containers --format "{{.ContainerName}}" | grep -qx "${nodebuilder}"; then
	echo "Pulling NodeJS runtime..."
	buildah from --name "${nodebuilder}" -v "${PWD}:/usr/src:Z" "${NODE_IMAGE}"
fi

echo "Build static UI files with node..."
buildah run \
	--workingdir=/usr/src/ui \
	"${nodebuilder}" \
	sh -c "corepack enable && yarn install --immutable && yarn build"

workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT

for key in $(python3 -c 'import json; print(" ".join(json.load(open("bridges.json"))))'); do
	reponame="matrix-${key}"
	image="$(python3 -c 'import json, sys; print(json.load(open("bridges.json"))[sys.argv[1]]["image"])' "${key}")"

	# Per-image metadata of the UI (name and description in the cluster admin)
	rm -rf "${workdir:?}/ui" && cp -a ui/dist "${workdir}/ui"
	python3 - "${key}" "${workdir}/ui/metadata.json" <<'PY'
import json, sys
key, path = sys.argv[1], sys.argv[2]
info = json.load(open("bridges.json"))[key]
meta = json.load(open(path))
meta["name"] = f"matrix-{key}"
meta["description"] = {
    "en": info["description"] + " Needs the matrix-reworked module on the same node.",
    "de": info["description"] + " Braucht das Modul matrix-reworked auf demselben Knoten.",
}
meta["source"] = f"ghcr.io/platypuschan/matrix-{key}"
json.dump(meta, open(path, "w"), indent=2, ensure_ascii=False)
PY
	printf '%s\n' "${key}" > "${workdir}/bridge"

	container=$(buildah from scratch)
	buildah add "${container}" imageroot /imageroot
	buildah add "${container}" bridges.json /imageroot/etc/bridges.json
	buildah add "${container}" "${workdir}/bridge" /imageroot/etc/bridge
	buildah add "${container}" "${workdir}/ui" /ui
	# One TCP port (the appservice listener); the role appserviceadm of every
	# matrix-reworked instance (the bridge uses the one of its node).
	buildah config --entrypoint=/ \
		--label="org.opencontainers.image.source=${repository_source}" \
		--label="org.opencontainers.image.revision=${revision}" \
		--label="org.nethserver.authorizations=matrix-reworked@any:appserviceadm" \
		--label="org.nethserver.tcp-ports-demand=1" \
		--label="org.nethserver.rootfull=0" \
		--label="org.nethserver.images=${image}" \
		"${container}"
	buildah commit "${container}" "${repobase}/${reponame}"
	images+=("${repobase}/${reponame}")
done

if [[ -n "${CI}" ]]; then
	printf "images=%s\n" "${images[*],,}" >>"${GITHUB_OUTPUT}"
else
	printf "Publish the images with:\n\n"
	for image in "${images[@],,}"; do printf "  buildah push %s docker://%s:%s\n" "${image}" "${image}" "${IMAGETAG:-latest}"; done
	printf "\n"
fi
