import numpy as np
import pytest

from delivery.base import BaseDeliveryChannel, DeliveryResult
from qc.base import BaseQCStage, QCStageResult
from scanning.base import BaseScannerSource, ScannerCapabilities
from separation.base import BaseRuleAction


@pytest.mark.parametrize("abc", [BaseScannerSource, BaseQCStage,
                                 BaseDeliveryChannel, BaseRuleAction])
def test_abcs_not_instantiable(abc):
    with pytest.raises(TypeError):
        abc()


def test_concrete_scanner_source():
    class Fake(BaseScannerSource):
        def list_devices(self): return ["fake"]
        def connect(self, device_name): pass
        def get_capabilities(self):
            return ScannerCapabilities(600, True, True)
        def acquire(self, dpi, color, duplex): return [b"png"]
        def disconnect(self): pass

    s = Fake()
    assert s.get_capabilities().max_dpi == 600
    assert s.acquire(300, "grayscale", False) == [b"png"]


def test_concrete_qc_stage():
    class Fake(BaseQCStage):
        @property
        def name(self): return "fake"
        def run(self, image, context):
            return QCStageResult(stage=self.name, deduction=5)

    r = Fake().run(np.zeros((10, 10), np.uint8), {})
    assert r.deduction == 5 and r.image is None and not r.auto_fail


def test_concrete_delivery_channel():
    class Fake(BaseDeliveryChannel):
        status_value = "delivered_local"
        @property
        def name(self): return "fake"
        def is_available(self): return True
        def deliver(self, doc_path, metadata):
            return DeliveryResult(True, self.name, doc_path)

    r = Fake().deliver("c:/x.pdf", {})
    assert r.success and r.channel == "fake"
