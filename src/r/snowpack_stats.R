# snowpack_stats.R
# Mt. Rainier basin snowpack analysis
# Reads SNOTEL data and produces stats + plots
# ─────────────────────────────────────────────

library(ggplot2)
library(dplyr)

# ── Load data ─────────────────────────────────
df <- read.csv("data/processed/snotel_wy2026.csv", stringsAsFactors = FALSE)
df$date <- as.Date(df$date)

cat("Loaded", nrow(df), "rows |", length(unique(df$station_name)), "stations\n")
cat("Date range:", as.character(min(df$date)), "→", as.character(max(df$date)), "\n\n")

# ── Basin average by day ───────────────────────
basin <- df %>%
  group_by(date) %>%
  summarise(
    basin_swe   = mean(swe_in,   na.rm = TRUE),
    basin_depth = mean(depth_in, na.rm = TRUE),
    basin_temp  = mean(temp_f,   na.rm = TRUE),
    n_stations  = sum(!is.na(swe_in)),
    .groups = "drop"
  ) %>%
  arrange(date) %>%
  mutate(
    wy_day    = as.integer(date - as.Date("2025-10-01")) + 1,
    swe_delta = basin_swe - lag(basin_swe, 1)
  )

# ── Latest snapshot ────────────────────────────
latest_date <- max(df$date)
latest <- df %>% filter(date == latest_date) %>% arrange(desc(elevation_ft))

cat("=== Latest Conditions:", as.character(latest_date), "===\n")
print(latest[, c("station_name", "elevation_ft", "swe_in", "depth_in", "temp_f")])

# ── Basin summary ──────────────────────────────
last <- tail(basin, 1)
cat("\n=== Basin Summary ===\n")
cat("Basin avg SWE:  ", round(last$basin_swe,   1), "in\n")
cat("Basin avg depth:", round(last$basin_depth, 1), "in\n")
cat("Basin avg temp: ", round(last$basin_temp,  1), "°F\n")
cat("Daily SWE change:", round(last$swe_delta,  2), "in\n")

if (!is.na(last$swe_delta) && last$swe_delta < -0.25) {
  cat("⚠  MELT SIGNAL: losing more than 0.25 in/day\n")
}

# ── Save CSVs ──────────────────────────────────
dir.create("data/processed", showWarnings = FALSE, recursive = TRUE)
dir.create("outputs",        showWarnings = FALSE, recursive = TRUE)

write.csv(basin,  "data/processed/basin_daily.csv",  row.names = FALSE)
write.csv(latest, "data/processed/station_latest_stats.csv", row.names = FALSE)
cat("\nSaved basin_daily.csv and station_latest_stats.csv\n")

# ── Plot 1: Basin SWE time series ──────────────
p1 <- ggplot(basin, aes(x = date, y = basin_swe)) +
  geom_area(fill = "#4fc3f7", alpha = 0.2) +
  geom_line(color = "#81d4fa", linewidth = 1) +
  geom_point(data = tail(basin, 1), color = "#ffffff", size = 3) +
  scale_x_date(date_labels = "%b %d", date_breaks = "2 weeks") +
  labs(
    title    = "Mt. Rainier Basin — Snow Water Equivalent",
    subtitle = paste0("7-station average · WY2026 · through ", latest_date),
    x        = NULL,
    y        = "Basin Avg SWE (inches)",
    caption  = "Source: NRCS SNOTEL AWDB"
  ) +
  theme_minimal() +
  theme(
    plot.background  = element_rect(fill = "#0a1628", color = NA),
    panel.background = element_rect(fill = "#0a1628", color = NA),
    panel.grid       = element_line(color = "#1e3a5f"),
    text             = element_text(color = "#cdd6f4"),
    axis.text        = element_text(color = "#6b7db3"),
    axis.text.x      = element_text(angle = 30, hjust = 1),
    plot.title       = element_text(color = "white",   size = 14, face = "bold"),
    plot.subtitle    = element_text(color = "#4fc3f7", size = 9),
    plot.caption     = element_text(color = "#6b7db3", size = 7),
  )

ggsave("outputs/basin_swe_timeseries.png", p1,
       width = 10, height = 5, dpi = 150, bg = "#0a1628")
cat("Saved: outputs/basin_swe_timeseries.png\n")

# ── Plot 2: Station SWE bars ───────────────────
p2 <- latest %>%
  mutate(station_name = reorder(station_name, elevation_ft)) %>%
  ggplot(aes(y = station_name, x = swe_in, fill = elevation_ft)) +
  geom_col(alpha = 0.85) +
  geom_text(aes(label = paste0(swe_in, " in")),
            hjust = -0.1, size = 3.2, color = "white") +
  scale_fill_gradient(low = "#4fc3f7", high = "#e8f4f8", name = "Elev (ft)") +
  scale_x_continuous(expand = expansion(mult = c(0, 0.18))) +
  labs(
    title    = "SWE by Station — Mt. Rainier Area",
    subtitle = as.character(latest_date),
    x        = "Snow Water Equivalent (inches)",
    y        = NULL,
    caption  = "Source: NRCS SNOTEL AWDB"
  ) +
  theme_minimal() +
  theme(
    plot.background  = element_rect(fill = "#0a1628", color = NA),
    panel.background = element_rect(fill = "#0a1628", color = NA),
    panel.grid       = element_line(color = "#1e3a5f"),
    text             = element_text(color = "#cdd6f4"),
    axis.text        = element_text(color = "#6b7db3"),
    plot.title       = element_text(color = "white",   size = 13, face = "bold"),
    plot.subtitle    = element_text(color = "#4fc3f7", size = 9),
    plot.caption     = element_text(color = "#6b7db3", size = 7),
    legend.background = element_rect(fill = "#0a1628", color = NA),
    legend.text      = element_text(color = "#cdd6f4"),
  )

ggsave("outputs/station_swe_bars.png", p2,
       width = 7, height = 5, dpi = 150, bg = "#0a1628")
cat("Saved: outputs/station_swe_bars.png\n")

# ── Plot 3: All stations time series ──────────
p3 <- ggplot(df, aes(x = date, y = swe_in,
                     color = reorder(station_name, elevation_ft),
                     group = station_name)) +
  geom_line(linewidth = 0.8, alpha = 0.9) +
  scale_color_manual(
    name   = "Station",
    values = c("#e8f4f8","#b3e5fc","#81d4fa","#4fc3f7","#29b6f6","#0288d1","#01579b")
  ) +
  scale_x_date(date_labels = "%b %d", date_breaks = "2 weeks") +
  labs(
    title    = "Snow Water Equivalent — All Stations",
    subtitle = "Mt. Rainier area · WY2026",
    x        = NULL,
    y        = "SWE (inches)",
    caption  = "Source: NRCS SNOTEL AWDB"
  ) +
  theme_minimal() +
  theme(
    plot.background  = element_rect(fill = "#0a1628", color = NA),
    panel.background = element_rect(fill = "#0a1628", color = NA),
    panel.grid       = element_line(color = "#1e3a5f"),
    text             = element_text(color = "#cdd6f4"),
    axis.text        = element_text(color = "#6b7db3"),
    axis.text.x      = element_text(angle = 30, hjust = 1),
    plot.title       = element_text(color = "white",   size = 14, face = "bold"),
    plot.subtitle    = element_text(color = "#4fc3f7", size = 9),
    plot.caption     = element_text(color = "#6b7db3", size = 7),
    legend.background = element_rect(fill = "#0a1628", color = NA),
    legend.text      = element_text(color = "#cdd6f4", size = 8),
    legend.key        = element_rect(fill = "#0a1628", color = NA),
  )

ggsave("outputs/all_stations_swe.png", p3,
       width = 10, height = 5, dpi = 150, bg = "#0a1628")
cat("Saved: outputs/all_stations_swe.png\n")

cat("\n=== snowpack_stats.R complete ===\n")