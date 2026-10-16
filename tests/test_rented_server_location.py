import sys
import unittest

sys.path.insert(0, '/ai')
import worker
import test_cloud_cpu

BULGARIA = {'id': '1', 'gpu': 'RTX 3090', 'price': .3778, 'machine_id': 150968, 'country': 'BG', 'region': 'europe',
            'location': 'Bulgaria, BG', 'vram_gb': 24.0, 'detail': '24 GB VRAM · ner 800 Mbit/s · Bulgaria, BG'}
SWEDEN = dict(BULGARIA, id='2', price=.3915, machine_id=45668, country='SE', location='Sweden, SE')


class RentedServerLocationTests(unittest.TestCase):
    """Dev job 50: Sweden was chosen in the plan, no Swedish server was free, Bulgaria was rented and nothing on
    the job said so."""

    def rent(self, offers, *, country=None, machine=None):
        job = {'id': 1}
        profile = {'gpu': 'RTX 3090', **({'country': country, 'machine_id': machine} if country else {})}
        result, error, *_ = test_cloud_cpu.RentCloudCpuTests().run_rent([{'status': 'ready'}], mode='cuda', offers=offers, job=job,
                                                         profile=profile)
        self.assertIsNone(error)
        return job

    def test_the_job_keeps_where_its_server_is(self):
        job = self.rent([BULGARIA])
        self.assertEqual({key: job['cloud_offer'][key] for key in ('gpu', 'location', 'country', 'machine_id')},
                         {'gpu': 'RTX 3090', 'location': 'Bulgaria, BG', 'country': 'BG', 'machine_id': 150968})
        self.assertIsNone(job['cloud_offer']['preferred_country'])

    def test_a_country_that_had_nothing_free_is_recorded_next_to_the_place_that_was_rented(self):
        job = self.rent([BULGARIA], country='SE', machine=140353)
        self.assertEqual((job['cloud_offer']['preferred_country'], job['cloud_offer']['country']), ('SE', 'BG'))
        label = worker.cloud_hardware_label('cloud-vast', job)
        self.assertEqual(label, 'Moln-GPU (vast · RTX 3090 · Bulgaria, BG · inga lediga servrar i SE, '
                                'närmaste plats valdes)')

    def test_the_chosen_country_is_rented_first_when_a_server_there_is_free(self):
        job = self.rent([BULGARIA, SWEDEN], country='SE', machine=140353)
        self.assertEqual(job['cloud_offer']['country'], 'SE')
        self.assertEqual(worker.cloud_hardware_label('cloud-vast', job), 'Moln-GPU (vast · RTX 3090 · Sweden, SE)')


class HardwareLabelTests(unittest.TestCase):
    def test_without_a_recorded_server_the_label_is_the_provider_only(self):
        self.assertEqual(worker.cloud_hardware_label('cloud-vast', {'id': 1}), 'Moln-GPU (vast)')
        self.assertEqual(worker.cloud_hardware_label('cloud-runpod', {'cloud_offer': None}), 'Moln-GPU (runpod)')

    def test_missing_parts_are_left_out(self):
        job = {'cloud_offer': {'gpu': 'RTX 4090', 'location': None, 'country': None, 'preferred_country': None}}
        self.assertEqual(worker.cloud_hardware_label('cloud-runpod', job), 'Moln-GPU (runpod · RTX 4090)')

    def test_no_note_when_the_server_is_in_the_asked_for_country(self):
        job = {'cloud_offer': {'gpu': 'RTX 3090', 'location': 'Sweden, SE', 'country': 'SE', 'preferred_country': 'SE'}}
        self.assertNotIn('inga lediga', worker.cloud_hardware_label('cloud-vast', job))


if __name__ == '__main__':
    unittest.main()
