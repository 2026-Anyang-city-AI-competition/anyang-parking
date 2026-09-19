import asyncio
import unittest

from src.serve.background_refresh import BackgroundRefresher


class Service:
    def __init__(self):
        self.calls = []

    def refresh_from_db(self, force=False):
        self.calls.append(force)
        return {"model_status": "ready", "data_status": "fresh", "calls": len(self.calls)}


class Repository:
    def __init__(self):
        self.calls = []

    def refresh(self, force=False):
        self.calls.append(force)
        return {"status": "ready"}


class BackgroundRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_periodic_trigger_and_stop(self):
        service, access, gate = Service(), Repository(), Repository()
        refresher = BackgroundRefresher(service, access, gate, interval_seconds=60)
        await refresher.start()
        self.assertEqual(service.calls, [True])
        self.assertTrue(refresher.status()["running"])

        refresher.trigger()
        for _ in range(20):
            if len(service.calls) >= 2:
                break
            await asyncio.sleep(.01)
        self.assertEqual(service.calls, [True, False])
        self.assertEqual(refresher.service_status()["calls"], 2)

        await refresher.stop()
        self.assertFalse(refresher.status()["running"])

    async def test_refreshes_are_serialized(self):
        service, access, gate = Service(), Repository(), Repository()
        refresher = BackgroundRefresher(service, access, gate, interval_seconds=60)
        await asyncio.gather(refresher.refresh(), refresher.refresh())
        self.assertEqual(len(service.calls), 2)
        self.assertEqual(refresher.status()["cycles"], 2)


if __name__ == "__main__":
    unittest.main()
