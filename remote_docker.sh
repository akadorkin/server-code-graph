#!/bin/sh
# Runs ON the server: one JSON line per container (compose dir, image, mounts, ports). No env vars — they hold secrets.
command -v docker >/dev/null 2>&1 || exit 0
docker ps -aq | xargs -r docker inspect --format '{"name":{{json .Name}},"image":{{json .Config.Image}},"state":{{json .State.Status}},"restart":{{json .HostConfig.RestartPolicy.Name}},"workdir":{{json (index .Config.Labels "com.docker.compose.project.working_dir")}},"ports":{{json .HostConfig.PortBindings}},"net":{{json .HostConfig.NetworkMode}},"mounts":[{{range $i,$m := .Mounts}}{{if $i}},{{end}}{{json $m.Source}}{{end}}]}'
