(function () {
  "use strict";

  function closeMenu(btn, menu) {
    if (!btn || !menu) return;
    menu.classList.remove("open");
    btn.setAttribute("aria-expanded", "false");
  }

  function openMenu(btn, menu) {
    if (!btn || !menu) return;
    menu.classList.add("open");
    btn.setAttribute("aria-expanded", "true");
  }

  function toggleMenu(btn, menu) {
    if (!btn || !menu) return;
    if (menu.classList.contains("open")) {
      closeMenu(btn, menu);
    } else {
      openMenu(btn, menu);
    }
  }

  function redirectAfterSelect(data, switcher) {
    const loc = window.location;
    const path = (loc && loc.pathname) || "";
    const isStudioMain = path === "/broadcaster" || path === "/broadcaster/";
    const isStudioDashboard = path === "/dashboard" || path === "/studio/dashboard";

    if (isStudioMain) {
      const sameStudioUrl = path + ((loc && loc.search) || "") + ((loc && loc.hash) || "");
      window.location.href = sameStudioUrl;
      return;
    }

    if (isStudioDashboard) {
      window.location.href = "/broadcaster";
      return;
    }

    if (data && data.ok && data.redirect) {
      window.location.href = data.redirect;
      return;
    }

    const dashboardHref = (switcher && switcher.getAttribute("data-dashboard-href")) || "";
    if (loc && (path === "/dashboard")) {
      window.location.href = "/dashboard";
      return;
    }
    if (loc && (path === "/dashboard" || path === "/studio/dashboard") && dashboardHref) {
      window.location.href = "/broadcaster";
      return;
    }
    window.location.reload();
  }

  function initStationSwitcher(switcher) {
    if (!(switcher instanceof HTMLElement)) return;
    const btn = switcher.querySelector(".station-switcher-btn");
    const menu = switcher.querySelector(".station-switcher-menu");
    if (!btn || !menu) return;

    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      document.querySelectorAll(".station-switcher").forEach(function (otherSwitcher) {
        if (otherSwitcher !== switcher) {
          closeMenu(
            otherSwitcher.querySelector(".station-switcher-btn"),
            otherSwitcher.querySelector(".station-switcher-menu")
          );
        }
      });
      toggleMenu(btn, menu);
    });

    menu.addEventListener("click", function (e) {
      const target = e.target;
      if (!(target instanceof HTMLElement)) return;

      const dashboardLink = target.closest(".station-switcher-dashboard");
      if (dashboardLink) {
        const href = (dashboardLink.getAttribute("href") || "").trim();
        closeMenu(btn, menu);
        if (href) {
          e.preventDefault();
          e.stopPropagation();
          window.location.href = href;
        }
        return;
      }

      const stationBtn = target.closest("[data-station-id]");
      if (!stationBtn) return;

      e.preventDefault();
      e.stopPropagation();

      const stationId = (stationBtn.getAttribute("data-station-id") || "").trim();
      if (!stationId) {
        closeMenu(btn, menu);
        return;
      }

      closeMenu(btn, menu);

      fetch("/stations/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ station_id: stationId }),
      })
        .then(function (r) {
          return r.json().catch(function () {
            return { ok: false };
          });
        })
        .then(function (data) {
          redirectAfterSelect(data, switcher);
        })
        .catch(function () {
          redirectAfterSelect(null, switcher);
        });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    const switchers = Array.from(document.querySelectorAll(".station-switcher"));
    if (!switchers.length) return;

    switchers.forEach(initStationSwitcher);

    document.addEventListener("click", function () {
      switchers.forEach(function (switcher) {
        closeMenu(
          switcher.querySelector(".station-switcher-btn"),
          switcher.querySelector(".station-switcher-menu")
        );
      });
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        switchers.forEach(function (switcher) {
          closeMenu(
            switcher.querySelector(".station-switcher-btn"),
            switcher.querySelector(".station-switcher-menu")
          );
        });
      }
    });
  });
})();
