from types import SimpleNamespace

from src.core.workspace import build_partition_layout


def test_build_partition_layout_uses_stock_and_port_sources():
    stock = object()
    port = object()
    context = SimpleNamespace(stock=stock, port=port)

    layout = build_partition_layout(context)

    assert layout["vendor"] is stock
    assert layout["odm_dlkm"] is stock
    assert layout["system"] is port
    assert layout["product_dlkm"] is port
