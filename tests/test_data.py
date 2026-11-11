from io import BytesIO

import pandas as pd
import pytest

from timepilot.data import DataValidationError, create_dataset, get_dataset, normalize_frame


def sample(rows=3):
    return pd.DataFrame({"series_id": ["A"] * rows,
                         "timestamp": pd.date_range("2023-01-01", periods=rows, freq="MS", tz="UTC"),
                         "target": [5., 6., 7.][:rows],
                         "available_at": pd.date_range("2023-02-10", periods=rows, freq="MS", tz="UTC")})


def test_basic_normalization():
    frame, issues = normalize_frame(sample())
    assert len(frame) == 3
    assert list(frame.columns) == ["series_id", "timestamp", "target", "available_at"]
    assert not issues


def test_missing_available_at_blocked():
    with pytest.raises(DataValidationError, match="available_at"):
        normalize_frame(sample().drop(columns="available_at"))


def test_declared_publication_lag_warns():
    frame, issues = normalize_frame(sample().drop(columns="available_at"), publication_lag_days=40)
    assert len(frame) == 3 and issues[0].code == "DATA-AVAIL-ASSUMED"


def test_duplicate_time_rejected():
    df = pd.concat([sample(), sample().iloc[[1]]], ignore_index=True)
    with pytest.raises(DataValidationError, match="DATA-DUP-001"):
        normalize_frame(df)


def test_negative_target_preserved():
    df = sample()
    df.loc[1, "target"] = -3
    normalized, warnings = normalize_frame(df)
    assert -3 in normalized.target.tolist()
    assert any(x.code == "DATA-NEG-001" for x in warnings)


def test_immutable_snapshot_checksum(client):
    import io
    source = sample().to_csv(index=False).encode()
    manifest = create_dataset(client.store, client.settings, source, "sample.csv", name="example")
    _, frame = get_dataset(client.store, client.settings, manifest.dataset_id)
    assert len(frame) == 3
    path = client.settings.home / "datasets" / manifest.dataset_id / "observations.csv"
    path.write_bytes(path.read_bytes() + b"BAD")
    with pytest.raises(DataValidationError, match="CHECKSUM"):
        get_dataset(client.store, client.settings, manifest.dataset_id)


def test_duplicate_dataset_name_not_overwritten(client):
    data = sample().to_csv(index=False).encode()
    create_dataset(client.store, client.settings, data, "sample.csv", name="example")
    with pytest.raises(DataValidationError, match="DATA-NAME-002"):
        create_dataset(client.store, client.settings, data, "sample.csv", name="example")


def test_excel_standardization(client):
    buf = BytesIO()
    excel_frame = sample()
    for col in ["timestamp", "available_at"]:
        excel_frame[col] = excel_frame[col].dt.tz_localize(None)
    excel_frame.to_excel(buf, index=False)
    manifest = create_dataset(client.store, client.settings, buf.getvalue(), "sample.xlsx", name="excel")
    assert manifest.record_count == 3


def test_csv_keeps_leading_zeros_in_entity_id(client):
    content = ("series_id,timestamp,target,available_at\n"
               "00123,2023-01-01,10,2023-02-10T00:00:00Z\n").encode()
    manifest = create_dataset(client.store, client.settings, content, "test.csv", name="zero_id")
    _, df = get_dataset(client.store, client.settings, manifest.dataset_id)
    assert df.series_id.iloc[0] == "00123"


def test_month_label_does_not_shift_timezone_date():
    data = pd.DataFrame({"series_id": ["A"], "timestamp": ["2026-01-01T00:00:00+08:00"],
                         "target": [10], "available_at": ["2026-02-05T10:00:00+08:00"]})
    normalized, _ = normalize_frame(data, source_timezone="Asia/Shanghai", frequency="MS")
    assert normalized.timestamp.iloc[0].startswith("2026-01-01T00")
    assert normalized.available_at.iloc[0].startswith("2026-02-05T02")
