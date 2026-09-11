# Stale-answer traps

Queries whose popular answer is out of date. An engine passes when it surfaces the current answer and marks the old one as outdated.

stale-answer traps  (n=18)

engine            pass  current  flagged  structural  silent stale  latency ms  errors
--------------------------------------------------------------------------------------
moo_fast            0%       6%       0%          0%            6%     11991.7       0
```
USP gate: FAIL
  [FAIL] moo pass rate: best moo engine moo_fast flagged 0% of traps (bar 70%)
  [n/a ] margin over competitors: no competitor keys set, head-to-head not evaluated
```

## how do I get the current UTC time in Python

**Changed in python 3.12** (deprecated). Stale: datetime.utcnow() returns a naive datetime and is deprecated since 3.12. Current: datetime.now(timezone.utc) (or datetime.UTC) returns an aware datetime.

- **moo_fast**: miss - nothing matched

## how to package a Python project with distutils

**Changed in python 3.12** (removed). Stale: distutils was removed from the standard library in Python 3.12. Current: use setuptools with a pyproject.toml build backend.

- **moo_fast**: miss - nothing matched

## how do I get the asyncio event loop in Python

**Changed in python 3.12** (deprecated). Stale: asyncio.get_event_loop() outside a running loop is deprecated. Current: asyncio.run() at the entry point, asyncio.get_running_loop() inside a coroutine.

- **moo_fast**: miss - nothing matched

## how do I install a Python package from source with setup.py

**Changed in python setuptools 58.3 / pip 21** (deprecated). Stale: python setup.py install is deprecated. Current: pip install . (or pip install -e . for an editable install).

- **moo_fast**: miss - nothing matched

## how do I render a React app into the DOM

**Changed in react 18** (deprecated). Stale: ReactDOM.render was deprecated in React 18. Current: createRoot(container).render(<App />).

- **moo_fast**: miss - current answer present
  > it initially looks like this: ``` import { strictmode } from "react"; import { createroot } from "react-dom/client"; import… ``` import { strictmode } from "react"; import { createroot } from "react-dom/client"; import "./index.css"; impor

## when should I use componentWillMount in React

**Changed in react 16.3 deprecated, 17 renamed** (renamed). Stale: componentWillMount is a legacy lifecycle method. Current: use componentDidMount, or the UNSAFE_ prefixed name if you truly need it.

- **moo_fast**: miss - nothing matched

## how do I start my services with docker compose

**Changed in docker Compose V2 (V1 end of life July 2023)** (superseded). Stale: the standalone docker-compose (V1) binary is end of life. Current: docker compose up, the V2 plugin built into the Docker CLI.

- **moo_fast**: miss - nothing matched

## what version should I put at the top of my docker-compose.yml

**Changed in docker Compose Specification** (obsolete). Stale: a top-level version: "3.x" key. Current: the version key is obsolete and Compose warns about it.

- **moo_fast**: miss - nothing matched

## how do I enforce PodSecurityPolicy in Kubernetes

**Changed in kubernetes 1.25** (removed). Stale: PodSecurityPolicy was removed in Kubernetes 1.25. Current: Pod Security Admission with the Pod Security Standards, or a policy engine.

- **moo_fast**: miss - nothing matched

## how do I create a deployment with kubectl run

**Changed in kubernetes 1.18** (removed). Stale: kubectl run --generator was removed; kubectl run only creates pods now. Current: kubectl create deployment.

- **moo_fast**: miss - nothing matched

## how do I add a signing key for an apt repository

**Changed in debian Debian 11 / Ubuntu 22.04** (deprecated). Stale: apt-key add is deprecated and adds a globally trusted key. Current: store the key in /etc/apt/keyrings and reference it with signed-by.

- **moo_fast**: miss - nothing matched

## how do I enable HTTP/2 in nginx

**Changed in nginx 1.25.1** (deprecated). Stale: the http2 parameter of the listen directive is deprecated. Current: the separate http2 on; directive.

- **moo_fast**: miss - nothing matched

## how do I tune the MySQL query cache

**Changed in mysql 8.0** (removed). Stale: the query cache was removed in MySQL 8.0. Current: there is no query cache; size the InnoDB buffer pool instead.

- **moo_fast**: miss - repeats the old answer; **no warning**
  > mysql query optimization - geeksforgeeks [mysqld] query_cache_type = 1 query_cache_size = 16m with query caching enabled, frequently executed queries can be served from the ** cache**, leading to faster response times and reduced load on th

## what wal_level do I need for streaming replication in Postgres

**Changed in postgresql 9.6** (renamed). Stale: wal_level = archive and hot_standby were merged and removed as names. Current: wal_level = replica, which is the default.

- **moo_fast**: miss - nothing matched

## how do I configure a Redis replica

**Changed in redis 5.0** (renamed). Stale: SLAVEOF and slave-* config names are deprecated aliases. Current: REPLICAOF and the replica-* config names.

- **moo_fast**: miss - nothing matched

## how do I create and switch to a new git branch

**Changed in git 2.23** (superseded). Stale: git checkout -b still works but its overloaded behaviour is why it was split. Current: git switch -c, with git restore for files.

- **moo_fast**: miss - nothing matched

## how do I parse a URL in Node.js

**Changed in node 11** (deprecated). Stale: the legacy url.parse() API is deprecated. Current: the WHATWG URL class: new URL(input).

- **moo_fast**: miss - nothing matched

## how do I format a date in Java

**Changed in java 8** (superseded). Stale: SimpleDateFormat is not thread safe and predates java.time. Current: java.time with DateTimeFormatter.

- **moo_fast**: miss - nothing matched
