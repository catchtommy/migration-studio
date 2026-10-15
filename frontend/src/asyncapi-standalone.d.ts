/** The AsyncAPI viewer's standalone browser bundle (no types are published for it). */
declare module "@asyncapi/react-component/browser/standalone" {
  const AsyncApiStandalone: {
    render(props: { schema: string | object; config?: object }, container: Element): void;
    hydrate(props: { schema: string | object; config?: object }, container: Element): void;
  };
  export default AsyncApiStandalone;
}
