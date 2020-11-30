import {Story, Meta} from '@storybook/react/types-6-0';
import faker from 'faker';
import * as React from 'react';

import {DeletionDialog, Props as DeletionDialogProps} from 'src/runs/DeletionDialog';
import {ApolloTestProvider} from 'src/testing/ApolloTestProvider';

// eslint-disable-next-line import/no-default-export
export default {
  title: 'DeletionDialog',
  component: DeletionDialog,
} as Meta;

const Template: Story<DeletionDialogProps & {mocks?: any}> = ({mocks, ...props}) => (
  <ApolloTestProvider mocks={mocks}>
    <DeletionDialog {...props} />
  </ApolloTestProvider>
);

const ids = [
  faker.random.uuid().slice(0, 8),
  faker.random.uuid().slice(0, 8),
  faker.random.uuid().slice(0, 8),
];

export const Success = Template.bind({});
Success.args = {
  isOpen: true,
  onClose: () => {
    console.log('Close!');
  },
  onTerminateInstead: () => {
    console.log('Terminate instead!');
  },
  selectedIDs: ids,
  terminatableIDs: [ids[0]],
  mocks: {
    Mutation: () => ({
      deletePipelineRun: () => ({
        __typename: 'DeletePipelineRunSuccess',
      }),
    }),
  },
};

export const WithError = Template.bind({});
WithError.args = {
  isOpen: true,
  onClose: () => {
    console.log('Close!');
  },
  onTerminateInstead: () => {
    console.log('Terminate instead!');
  },
  selectedIDs: ids,
  terminatableIDs: [ids[0]],
  mocks: {
    Mutation: () => ({
      deletePipelineRun: (args: {runId: string}) => {
        // Fail the last run
        if (args.runId === ids[2]) {
          return {
            __typename: 'PythonError',
            message: 'Oh no!',
          };
        }
        return {
          __typename: 'DeletePipelineRunSuccess',
        };
      },
    }),
  },
};
