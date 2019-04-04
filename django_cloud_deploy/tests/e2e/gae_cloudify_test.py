# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""End to end test for deploying an existing project using Google App Engine."""

import os
import subprocess
import types
import unittest
import urllib.parse

import backoff
from django_cloud_deploy.cli import cloudify
from django_cloud_deploy.cli import io
from django_cloud_deploy.cli import update
from django_cloud_deploy.tests.lib import test_base
from django_cloud_deploy.tests.lib import utils
import requests
from selenium import webdriver
from selenium.webdriver.chrome import options


class GAECloudifyAndUpdateE2ETest(test_base.ResourceCleanUp):
    """End to end test for deploying an existing Django project on GAE."""

    _CLOUDSQL_ROLES = ('roles/cloudsql.client', 'roles/cloudsql.editor',
                       'roles/cloudsql.admin')

    _FAKE_CLOUDSQL_SERVICE_ACCOUNT = {
        'id': utils.get_resource_name('sa'),
        'name': 'Fake CloudSQL Credentials',
        'file_name': 'credentials.json',
        'roles': _CLOUDSQL_ROLES
    }

    def setUp(self):
        super().setUp()
        self.project_dir = self._get_django_project_path()

    def _get_django_project_path(self):
        """Returns the absolute path of an existing Django project.

        This project exists under
        django_cloud_deply/tests/e2e/data/basic_django_project
        """
        dirname = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(dirname, 'data', 'basic_django_project')

    @staticmethod
    @backoff.on_exception(
        backoff.expo, requests.exceptions.ConnectionError, max_tries=3)
    def _get_with_retry(url: str) -> requests.models.Response:
        return requests.get(url)

    @backoff.on_predicate(backoff.expo, logger=None, max_tries=5)
    def _wait_for_appengine_update_ready(self) -> bool:
        """Returns when 100% traffic is put to the latest app.

        Otherwise retries for at most 5 times and returns.
        """
        command = [
            'gcloud', '--project=' + self.project_id, 'app', 'versions', 'list',
            '--sort-by=LAST_DEPLOYED', '--format=csv[no-heading](TRAFFIC_SPLIT)'
        ]
        result = subprocess.check_output(
            command, universal_newlines=True).rstrip().splitlines()
        if len(result) <= 1:
            return False
        traffic = float(result[-1])
        return traffic == 1.0

    @unittest.mock.patch('portpicker.pick_unused_port', return_value=5432)
    def test_cloudify_and_update_new_project(self, unused_mock):
        # Generate unique resource names
        fake_superuser_name = 'admin'
        fake_password = 'fake_password'

        cloud_storage_bucket_name = utils.get_resource_name('bucket')
        database_instance_name = utils.get_resource_name('sql-instance')
        service_name = utils.get_resource_name('svc', delimiter='')

        # Generate names we hardcode for users
        service_account_email = '{}@{}.iam.gserviceaccount.com'.format(
            self._FAKE_CLOUDSQL_SERVICE_ACCOUNT['id'], self.project_id)
        member = 'serviceAccount:{}'.format(service_account_email)

        settings_path = os.path.join(self.project_dir, 'mysite', 'settings.py')
        requirements_path = os.path.join(self.project_dir, 'requirements.txt')
        with self.clean_up_bucket(cloud_storage_bucket_name), \
                self.delete_service_account(service_account_email), \
                self.reset_iam_policy(member, self._CLOUDSQL_ROLES), \
                self.clean_up_sql_instance(database_instance_name), \
                self.clean_up_appengine_service(service_name):

            test_io = io.TestIO()
            test_io.answers.append(self.project_id)  # project_id
            test_io.password_answers.append(fake_password)  # database password
            # database password again
            test_io.password_answers.append(fake_password)
            # django_directory_path_cloudify
            test_io.answers.append(self.project_dir)
            # django_requirements_path
            test_io.answers.append(requirements_path)
            # django_settings_path
            test_io.answers.append(settings_path)
            # django_superuser_login
            test_io.answers.append(fake_superuser_name)
            # django_superuser_password
            test_io.password_answers.append(fake_password)
            # django_superuser_password again
            test_io.password_answers.append(fake_password)
            test_io.answers.append('')  # django_superuser_email

            test_io.answers.append('N')  # Do not do survey at the end

            fake_service_accounts = {
                'cloud_sql': [self._FAKE_CLOUDSQL_SERVICE_ACCOUNT]
            }

            arguments = types.SimpleNamespace(
                credentials=self.credentials,
                use_existing_project=True,
                bucket_name=cloud_storage_bucket_name,
                service_accounts=fake_service_accounts,
                appengine_service_name=service_name,
                database_instance_name=database_instance_name,
                backend='gae')
            url = cloudify.main(arguments, test_io)

            # Assert answers are all used.
            self.assertEqual(len(test_io.answers), 0)
            self.assertEqual(len(test_io.password_answers), 0)

            # Setup Selenium
            chrome_options = options.Options()
            chrome_options.add_argument('--headless')  # No browser
            driver = webdriver.Chrome(options=chrome_options)

            # Assert the web app is available
            driver.get(url)
            self.assertIn('Hello from the Cloud!', driver.page_source)

            # Assert the web app admin page is available
            admin_url = urllib.parse.urljoin(url, '/admin')
            driver.get(admin_url)
            self.assertEqual(driver.title, 'Log in | Django site admin')

            # Log in with superuser name and password
            username = driver.find_element_by_id('id_username')
            password = driver.find_element_by_id('id_password')
            username.send_keys(fake_superuser_name)
            password.send_keys(fake_password)
            driver.find_element_by_css_selector(
                '.submit-row [value="Log in"]').click()
            self.assertEqual(driver.title,
                             'Site administration | Django site admin')

            object_path = 'static/admin/css/base.css'
            object_url = 'http://storage.googleapis.com/{}/{}'.format(
                cloud_storage_bucket_name, object_path)
            response = requests.get(object_url)

            # Assert the static content is successfully uploaded
            self.assertIn('DJANGO', response.text)

            # Assert the deployed app is using static content from the GCS
            # bucket
            self.assertIn(cloud_storage_bucket_name, driver.page_source)

            # Test update command
            test_io = io.TestIO()
            test_io.password_answers.append(fake_password)  # database password
            test_io.password_answers.append(fake_password)  # Confirm password
            test_io.answers.append(self.project_dir)  # django_directory_path

            view_file_path = os.path.join(self.project_dir, 'polls', 'views.py')
            with open(view_file_path) as view_file:
                file_content = view_file.read()
                file_content = file_content.replace('Hello', 'Hello1')

            with open(view_file_path, 'w') as view_file:
                view_file.write(file_content)
            arguments = types.SimpleNamespace(
                credentials=self.credentials,
                database_instance_name=database_instance_name)

            update.main(arguments, test_io)

            # Assert answers are all used.
            self.assertEqual(len(test_io.answers), 0)
            self.assertEqual(len(test_io.password_answers), 0)

            self._wait_for_appengine_update_ready()
            response = self._get_with_retry(url)
            self.assertIn('Hello1 from the Cloud!', response.text)