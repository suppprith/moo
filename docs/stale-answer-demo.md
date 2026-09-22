# Stale-answer traps

Queries whose popular answer is out of date. An engine passes when it surfaces the current answer and marks the old one as outdated.

stale-answer traps  (n=29)

engine            pass  current  flagged  structural  silent stale  latency ms  errors
--------------------------------------------------------------------------------------
moo_fast           28%      72%      31%          0%           21%     13941.0       0

by cohort        cohort    n    pass  current  flagged
moo_fast        classic   18     33%      78%      39%
moo_fast         recent   11     18%      64%      18%
```
USP gate: FAIL
  [FAIL] moo pass rate: best moo engine moo_fast flagged 28% of traps (bar 70%)
  [FAIL] recent-change pass rate: best moo engine moo_fast caught 18% of changes a model can't know from training (bar 70%)
  [n/a ] margin over competitors: no competitor keys set, head-to-head not evaluated
```

## how do I get the current UTC time in Python

**Changed in python 3.12** (deprecated). Stale: datetime.utcnow() returns a naive datetime and is deprecated since 3.12. Current: datetime.now(timezone.utc) (or datetime.UTC) returns an aware datetime.

- **moo_fast**: pass - current answer present; repeats the old answer; textual flag (no longer, obsolete)
  > h on jan 1, 1970? ## answer (score 377, 2025-03-26) for python 3, use `datetime.now(timezone.utc)` to get a timezone-aware datetime, and use `.timestamp()` to convert it to a timestamp. `… # how to get utc time in python? how do i get the u

## how to package a Python project with distutils

**Changed in python 3.12** (removed). Stale: distutils was removed from the standard library in Python 3.12. Current: use setuptools with a pyproject.toml build backend.

- **moo_fast**: pass - current answer present; repeats the old answer; textual flag (outdated)
  > tional details on configuring, packaging and distributing python projects with `setuptools` that aren’t covered by the introductory tutorial in packaging py… # packaging and distributing projects¶ - page status: - outdated - last reviewed

## how do I get the asyncio event loop in Python

**Changed in python 3.12** (deprecated). Stale: asyncio.get_event_loop() outside a running loop is deprecated. Current: asyncio.run() at the entry point, asyncio.get_running_loop() inside a coroutine.

- **moo_fast**: pass - current answer present; repeats the old answer; textual flag (instead of)
  > ntax. ``` $ python -m asyncio asyncio repl ... use "await" directly instead of "asyncio.run()". type "help", "copyright", "credits" or "license" for more information. >>> import asyncio >>> await asyncio.sleep(10, result='hello') 'hello' ``

## how do I install a Python package from source with setup.py

**Changed in python setuptools 58.3 / pip 21** (deprecated). Stale: python setup.py install is deprecated. Current: pip install . (or pip install -e . for an editable install).

- **moo_fast**: miss - repeats the old answer; textual flag (legacy)
  > me='foo', version='1.0', py_modules=['foo'], ) ``` ``` python setup.py install ``` installing python modules (legacy version)¶ ### the new standard: distutils¶ if you download a module source distribution, you can tell pre

## how do I render a React app into the DOM

**Changed in react 18** (deprecated). Stale: ReactDOM.render was deprecated in React 18. Current: createRoot(container).render(<App />).

- **moo_fast**: miss - current answer present
  > it initially looks like this: ``` import { strictmode } from "react"; import { createroot } from "react-dom/client"; import… ``` import { strictmode } from "react"; import { createroot } from "react-dom/client"; import "./index.css"; impor

## when should I use componentWillMount in React

**Changed in react 16.3 deprecated, 17 renamed** (renamed). Stale: componentWillMount is a legacy lifecycle method. Current: use componentDidMount, or the UNSAFE_ prefixed name if you truly need it.

- **moo_fast**: miss - current answer present; repeats the old answer; **no warning**
  > `props` - `state` - `constructor(props)` - `componentdidcatch(error, info)` - `componentdidmount()` - `componentdidupdate(prevprops, prevstate, snapshot?)` - `componentwillmount()` - `componentwillreceiveprops(nextprops)` - `componentwillu

## how do I start my services with docker compose

**Changed in docker Compose V2 (V1 end of life July 2023)** (superseded). Stale: the standalone docker-compose (V1) binary is end of life. Current: docker compose up, the V2 plugin built into the Docker CLI.

- **moo_fast**: miss - current answer present; repeats the old answer; **no warning**
  > volumes in a single yaml configuration file. - start up your application: - `$ docker compose up`- with a single command, you create and start all the services from your configuration file. define services in docker compose compose guarant

## what version should I put at the top of my docker-compose.yml

**Changed in docker Compose Specification** (obsolete). Stale: a top-level version: "3.x" key. Current: the version key is obsolete and Compose warns about it.

- **moo_fast**: miss - current answer present
  > ersion` top-level element in the `compose.yaml` file and relies entirely on the compose specification to interpret the file. history and development of docker compose ### compose file format versioning the docker compose clis are defined by

## how do I enforce PodSecurityPolicy in Kubernetes

**Changed in kubernetes 1.25** (removed). Stale: PodSecurityPolicy was removed in Kubernetes 1.25. Current: Pod Security Admission with the Pod Security Standards, or a policy engine.

- **moo_fast**: pass - current answer present; repeats the old answer; textual flag (deprecated)
  > mission controllers as follows: - whenever you create a new cluster, enable the pod security admission controller. - immediately after creating a new cluster, create pod security policies, along with roles (or clusterroles) and ro… **cautio

## how do I create a deployment with kubectl run

**Changed in kubernetes 1.18** (removed). Stale: kubectl run --generator was removed; kubectl run only creates pods now. Current: kubectl create deployment.

- **moo_fast**: miss - current answer present
  > kubectl create deployment create a deployment with the specified name create a deployment with the specified name. ``` kubectl create deployment name --image=image -- [command] [args...] ``` ``` # create a deployment named my-dep that runs

## how do I add a signing key for an apt repository

**Changed in debian Debian 11 / Ubuntu 22.04** (deprecated). Stale: apt-key add is deprecated and adds a globally trusted key. Current: store the key in /etc/apt/keyrings and reference it with signed-by.

- **moo_fast**: pass - current answer present; repeats the old answer; textual flag (deprecated)
  > fetch the latest repository signing key: - download the key: - `sudo mkdir -p /etc/apt/keyrings sudo curl --fail --silent --show-error \ --output /etc/apt/keyrings/gitlab-keyring.asc \ --url "https://packages.gitlab.com/gpgkey/gpg.key"` -

## how do I enable HTTP/2 in nginx

**Changed in nginx 1.25.1** (deprecated). Stale: the http2 parameter of the listen directive is deprecated. Current: the separate http2 on; directive.

- **moo_fast**: miss - nothing matched

## how do I tune the MySQL query cache

**Changed in mysql 8.0** (removed). Stale: the query cache was removed in MySQL 8.0. Current: there is no query cache; size the InnoDB buffer pool instead.

- **moo_fast**: miss - repeats the old answer; **no warning**
  > eful with setting… ## answer (score 6, 2023-06-19) be careful with setting the query_cache_size and limit too high. mysql only uses a single thread to read from the query cache. mysql query optimization - geeksforgeeks [mysqld] query_cache

## what wal_level do I need for streaming replication in Postgres

**Changed in postgresql 9.6** (renamed). Stale: wal_level = archive and hot_standby were merged and removed as names. Current: wal_level = replica, which is the default.

- **moo_fast**: miss - nothing matched

## how do I configure a Redis replica

**Changed in redis 5.0** (renamed). Stale: SLAVEOF and slave-* config names are deprecated aliases. Current: REPLICAOF and the replica-* config names.

- **moo_fast**: miss - current answer present
  > bind` address pointing to a local ip that your other machines ## can reach you. replicaof 10.0.0.1 6379` - restart the redis service for the changes to take effect. redis replication and failover providing your own instance ### example conf

## how do I create and switch to a new git branch

**Changed in git 2.23** (superseded). Stale: git checkout -b still works but its overloaded behaviour is why it was split. Current: git switch -c, with git restore for files.

- **moo_fast**: miss - current answer present
  > m any commit. for example, switch to "`head~3`" and create branch "`fixup`": $ git switch -c fixup head~3 switched to a new branch 'fixup' if you want to start a new branch from a remote branch of the same name: $ git switch new-topic br

## how do I parse a URL in Node.js

**Changed in node 11** (deprecated). Stale: the legacy url.parse() API is deprecated. Current: the WHATWG URL class: new URL(input).

- **moo_fast**: pass - current answer present; repeats the old answer; textual flag (legacy)
  > pathname = '/a/b/c'; const search = '?d=e'; const hash = '#fgh'; const myurl = new url(`https://example.org${pathname}${search}${hash}`); ``` node.js v26.10.0 documentation parses a string as a url. if `base` is provided, it will be used a

## how do I format a date in Java

**Changed in java 8** (superseded). Stale: SimpleDateFormat is not thread safe and predates java.time. Current: java.time with DateTimeFormatter.

- **moo_fast**: miss - current answer present; repeats the old answer; **no warning**
  > class datetimeformatter #### iso_datepublic static final datetimeformatter iso_date the iso date formatter that formats or parses a date with the offset if available, such as '2011-12-03' or '2011-12-03+01:00'.this returns an immutable form

## what is the default multiprocessing start method on Linux in Python (recent)

**Changed in python 3.14** (changed default). Stale: fork is the default start method on Linux. Current: forkserver is the default on Unix platforms other than macOS; fork must be requested explicitly.

- **moo_fast**: miss - current answer present
  > ich is the default. the possible start methods are - `'fork'`,- `'spawn'`and- `'forkserver'`. not all platforms support all methods. see contexts and start methods.- added i… - multiprocessing.get_all_start_methods()¶ - returns a list of th

## how do I generate a UUIDv7 in Postgres (recent)

**Changed in postgresql 18** (superseded). Stale: install the pg_uuidv7 extension or write your own PL/pgSQL function. Current: PostgreSQL 18 has a built-in uuidv7() function.

- **moo_fast**: miss - current answer present
  > # answer (score 1, accepted, 2025-12-04) ah. i figured it out. you need to add `uuidv7()` under the default section when creating a column like this: how can i do v7 using the gui? you need to add `uuidv7()` under the default section when c

## how do I set up md5 password authentication in pg_hba.conf (recent)

**Changed in postgresql 18** (deprecated). Stale: md5 password authentication is deprecated and will be removed in a future major version. Current: use scram-sha-256 password authentication.

- **moo_fast**: pass - current answer present; repeats the old answer; textual flag (no longer)
  > in text in the latter case). to upgrade an existing installation from `md5` to `scram-sha-256`, after having ensured that all client libraries in use are new enough to support scram, set `password_encryption = 'scram-sha-256'` in `postgresq

## how do I add middleware in Next.js (recent)

**Changed in nextjs 16** (renamed). Stale: middleware.ts is deprecated for the Node.js runtime. Current: rename middleware.ts to proxy.ts and the exported function to proxy.

- **moo_fast**: miss - repeats the old answer; **no warning**
  > nse; } return await au how to use multiple middlewares in next.js using the middleware.ts file? # how to use multiple middlewares in next.js using the middleware.ts file? i'm working on a next.js project and trying to implement multiple

## how do I lint a Next.js app (recent)

**Changed in nextjs 16** (removed). Stale: the next lint command was removed and next build no longer lints. Current: run ESLint or Biome directly (codemod: next-lint-to-eslint-cli).

- **moo_fast**: miss - repeats the old answer; **no warning**
  > existing middleware: how can i see lint messages in next dev (next.js) now, if `next lint` passes (no errors, only warnings), it runs `next dev`; otherwise, the process stops there until the errors are fixed. ## answer (score 0, 2024-01-25)

## how do I use useEffectEvent in React (recent)

**Changed in react 19.2** (stabilized). Stale: useEffectEvent is experimental and imported as experimental_useEffectEvent. Current: useEffectEvent ships in React 19.2.

- **moo_fast**: miss - nothing matched

## how do I install the ingress-nginx controller in Kubernetes (recent)

**Changed in kubernetes ingress-nginx retired March 2026** (retired). Stale: ingress-nginx is retired; maintenance ended in March 2026. Current: migrate to Gateway API, or to another Ingress controller.

- **moo_fast**: miss - current answer present
  > ingress controller will stop receiving maintenance in march 2026. - **set up a gateway api controller.**this option replaces the nginx ingress controller and the use of the ingress api with an implementation of the kubernetes gateway api.

## how do I set GOMAXPROCS for a Golang service running in a container (recent)

**Changed in go 1.25** (superseded). Stale: import go.uber.org/automaxprocs so GOMAXPROCS respects the container CPU limit. Current: Go 1.25 reads the cgroup CPU limit and sets GOMAXPROCS itself.

- **moo_fast**: miss - current answer present
  > ault we want go to provide efficient and reliable defaults when possible, so in go 1.25, we have made `gomaxprocs` take into account its container environment by default. if a go process is running inside a container with a cpu limit, `goma

## how do I create an npm automation token to publish from GitHub Actions (recent)

**Changed in npm classic tokens revoked 2025-12-09** (removed). Stale: classic (automation) tokens were revoked and can no longer be created. Current: trusted publishing (OIDC), or a granular access token.

- **moo_fast**: miss - current answer present
  > token: ${{ secrets.github_token }} fetch-depth: 0 - name: publ trusted publishing for npm packages | npm docs trusted publishing eliminates these risks by using short-lived, workflow-specific credentials that are automatica

## how do I configure ESLint with an .eslintrc file (recent)

**Changed in eslint 10** (removed). Stale: .eslintrc.* and .eslintignore files are no longer honored. Current: flat config in eslint.config.js.

- **moo_fast**: pass - current answer present; repeats the old answer; textual flag (no longer)
  > multiple c… this eslintrc file supports multiple configs with overrides: ``` // eslint.config.js import { defineconfig } from "eslint/config"; import js from "@eslint/js"; export default defineconfig([ js.configs.recommended, // recommende

## what moduleResolution should I set in tsconfig for a Node.js project (recent)

**Changed in typescript 6.0** (deprecated). Stale: --moduleResolution node (node10) is deprecated. Current: nodenext for code Node runs directly, bundler when a bundler resolves imports.

- **moo_fast**: miss - nothing matched
