# This is a template repo initializing new project

You can use it when you are creating repository.
Newly created repo doesn't retain previous commits of this repo.

## Actions

### Create Release

Automatically create release by pushing a tag into remote

```bash
git tag v0.1.0
git push origin v0.1.0
```

### Build a image

Automatically builds docker image on production server, once a tag is pushed.

### Test Dockerfile

host server's docker can be accessed from within dev container.

```bash
# to test
docker images
```

## To Run image

1. Create env file named `oracle-template.env` in `$HOME/envs/` directory
2. Run docker image

```bash
docker run --rm --env-file=$HOME/envs/oracle-template.env oracle-template:v0.1.0
```

## This repo includes

- Simple project structure
- Data frame minification function and logging/timer decorator
- Oracle database connection functions
- Dockerfile for deploying

## What to change after initializing new repo

- Change readme files
- Remove any unnecessary directories
- Add package versions in requirements files
- Make changes in Dockerfile
- Make changes in script.py

## How to make changes for this template repo

Only make changes for general settings that can be used by everyone
