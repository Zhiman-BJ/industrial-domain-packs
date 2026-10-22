// Maintained inputs: main uses prod; other branches use test. Export data only.
// GitHub Actions writes image references and Tool IDs to release locks.
// Credentials stay in Secrets; namespace and default Tool timeouts are fixed in code.
module.exports = {
  schemaVersion: 1,
  region: "ap-guangzhou",
  domains: {
    chip: {
      profile: "rtl-cpu",
      networkMode: "PUBLIC",
      command: ["/bin/bash"],
      args: [
        "-l",
        "-c",
        "/usr/bin/envd > /tmp/envd.log 2>&1 & exec python3 /opt/domain-packs/lib/worker-http.py",
      ],
      ports: [
        {
          name: "worker",
          port: 8080,
          protocol: "TCP",
        },
        {
          name: "envd",
          port: 49983,
          protocol: "TCP",
        },
      ],
      resources: {
        cpu: "2000m",
        memory: "2Gi",
        storage: "",
      },
      probe: {
        httpGet: {
          path: "/health",
          port: 49983,
          scheme: "HTTP",
        },
        readyTimeoutMs: 30000,
        probeTimeoutMs: 1000,
        probePeriodMs: 3000,
        successThreshold: 1,
        failureThreshold: 100,
      },
    },
    freecad: {
      profile: "freecad-linux-cpu",
      networkMode: "PUBLIC",
      command: ["/bin/bash", "-lc"],
      args: [
        "/usr/bin/envd >/tmp/envd.log 2>&1 & exec python3 /opt/domain-packs/lib/worker-http.py",
      ],
      ports: [
        {
          name: "worker",
          port: 8080,
          protocol: "TCP",
        },
        {
          name: "envd",
          port: 49983,
          protocol: "TCP",
        },
      ],
      resources: {
        cpu: "2",
        memory: "2Gi",
        storage: "10Gi",
      },
      probe: {
        httpGet: {
          path: "/health",
          port: 49983,
          scheme: "HTTP",
        },
        readyTimeoutMs: 10000,
        probeTimeoutMs: 1000,
        probePeriodMs: 1000,
        successThreshold: 1,
        failureThreshold: 3,
      },
    },
    pcb: {
      profile: "pcb-verify-cpu",
      networkMode: "PUBLIC",
      command: ["/bin/bash", "-lc"],
      args: [
        "/usr/bin/envd >/tmp/envd.log 2>&1 & exec python3 /opt/domain-packs/lib/worker-http.py",
      ],
      ports: [
        {
          name: "worker",
          port: 8080,
          protocol: "TCP",
        },
        {
          name: "envd",
          port: 49983,
          protocol: "TCP",
        },
      ],
      resources: {
        cpu: "2",
        memory: "2Gi",
        storage: "5Gi",
      },
      probe: {
        httpGet: {
          path: "/health",
          port: 49983,
          scheme: "HTTP",
        },
        readyTimeoutMs: 10000,
        probeTimeoutMs: 1000,
        probePeriodMs: 1000,
        successThreshold: 1,
        failureThreshold: 3,
      },
    },
  },
};
